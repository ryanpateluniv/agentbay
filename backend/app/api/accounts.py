"""Email + password accounts: signup, login, logout, and switching on the creator role.

Tokens are opaque `sess_...` strings sent as `Authorization: Bearer <token>`, the same scheme as the demo tokens.
Only their sha256 is stored. New accounts are buyers with TRIAL_CREDITS (default 500) as a promo ledger entry.
ADMIN_EMAILS grants admin only through Supabase sign-in, where the email is verified; a password signup can't
prove it owns the address, so it never gets admin."""
import re
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DB

from ..auth import SESSION_PREFIX, current_user, token_hash
from ..config import MICRO, settings
from ..db import get_db
from ..models import AuthSession, CreatorProfile, User, UserCredential, now
from ..services import ledger
from ..services.passwords import DUMMY_HASH, hash_password, verify_password

router = APIRouter(tags=["accounts"])
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_FAILS: dict[str, list[float]] = {}  # email -> recent failed login times (in-memory throttle)
MAX_FAILS, FAIL_WINDOW_S = 5, 300


class SignupIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(min_length=8, max_length=128)
    name: str = Field("", max_length=100)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=128)


class AccountUser(BaseModel):
    id: str
    email: str
    name: str
    roles: list[str]


class AuthOut(BaseModel):
    token: str = Field(description="send as Authorization: Bearer <token>")
    token_type: str = "bearer"
    expires_at: str
    user: AccountUser


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "roles": list(u.roles)}


def _issue(db: DB, user: User) -> dict:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    s = AuthSession(token_hash=token_hash(token), user_id=user.id, expires_at=now() + timedelta(days=settings.session_days))
    db.add(s)
    db.commit()
    return {"token": token, "token_type": "bearer", "expires_at": s.expires_at.isoformat() + "Z", "user": _user_out(user)}


def _bad_login():
    raise HTTPException(401, detail={"error": "invalid_credentials", "detail": "Email or password is wrong."})


@router.post("/v1/auth/signup", response_model=AuthOut, status_code=201,
             summary="Create an account (buyer, with trial credits) and return a session token")
def signup(body: SignupIn, db: DB = Depends(get_db)):
    email = body.email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(422, detail={"error": "invalid_email", "detail": "Enter a valid email address."})
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, detail={"error": "email_taken", "detail": "An account with this email exists. Log in."})
    user = User(email=email, name=body.name.strip() or email.split("@")[0], roles=["buyer"])
    db.add(user)
    try:
        db.flush()
        db.add(UserCredential(user_id=user.id, password_hash=hash_password(body.password)))
        db.commit()
    except IntegrityError:  # same email signed up concurrently
        db.rollback()
        raise HTTPException(409, detail={"error": "email_taken", "detail": "An account with this email exists. Log in."})
    ledger.grant_promo(db, user.id, settings.trial_credits * MICRO, f"trial:{user.id}")
    return _issue(db, user)


@router.post("/v1/auth/login", response_model=AuthOut, summary="Log in with email and password")
def login(body: LoginIn, db: DB = Depends(get_db)):
    email = body.email.strip().lower()
    recent = [t for t in _FAILS.get(email, []) if t > time.time() - FAIL_WINDOW_S]
    if len(recent) >= MAX_FAILS:
        raise HTTPException(429, detail={"error": "too_many_attempts", "detail": "Too many attempts. Try again in a few minutes."})
    user = db.scalar(select(User).where(User.email == email))
    cred = db.get(UserCredential, user.id) if user else None
    ok = verify_password(body.password, cred.password_hash if cred else DUMMY_HASH) and cred is not None
    if not ok or user.status != "active":
        _FAILS[email] = recent + [time.time()]
        _bad_login()
    _FAILS.pop(email, None)
    return _issue(db, user)


@router.post("/v1/auth/logout", summary="Revoke the session token in the Authorization header")
def logout(authorization: str = Header(""), db: DB = Depends(get_db), user: User = Depends(current_user)):
    token = authorization.removeprefix("Bearer ").strip()
    s = db.get(AuthSession, token_hash(token)) if token.startswith(SESSION_PREFIX) else None
    if s and s.revoked_at is None:
        s.revoked_at = now()
        db.commit()
    return {"ok": True}


@router.post("/v1/me/creator", response_model=AccountUser, summary="Switch on the creator role for this account")
def become_creator(db: DB = Depends(get_db), user: User = Depends(current_user)):
    if "creator" not in user.roles:
        user.roles = list(user.roles) + ["creator"]
    if db.get(CreatorProfile, user.id) is None:
        db.add(CreatorProfile(user_id=user.id, display_name=user.name or user.email.split("@")[0]))
    db.commit()
    return _user_out(user)
