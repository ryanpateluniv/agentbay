"""User auth: demo bearer tokens (tok_sam, ...) and, when configured, Supabase access tokens.

A Supabase token is verified by asking Supabase who it belongs to (GET {SUPABASE_URL}/auth/v1/user), which works
for any signing scheme. The email is mapped to a platform user, created on first sight with the free trial
credits (Section 7). ADMIN_EMAILS (comma list) get the admin role; everyone gets buyer + creator."""
import os
import time

import httpx
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .config import MICRO
from .db import get_db
from .models import CreatorProfile, User

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
FREE_TRIAL_CREDITS = 200
_cache: dict[str, tuple[float, str]] = {}  # token -> (expires_at, user_id)


def supabase_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_ANON_KEY)


def _supabase_user(db: DB, token: str) -> User | None:
    hit = _cache.get(token)
    if hit and hit[0] > time.time():
        return db.get(User, hit[1])
    try:
        r = httpx.get(f"{SUPABASE_URL}/auth/v1/user", timeout=5,
                      headers={"apikey": SUPABASE_ANON_KEY, "authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.json().get("email"):
        return None
    info = r.json()
    email = info["email"].lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        meta = info.get("user_metadata") or {}
        roles = ["buyer", "creator"] + (["admin"] if email in ADMIN_EMAILS else [])
        user = User(email=email, name=meta.get("full_name") or meta.get("name") or email.split("@")[0], roles=roles)
        db.add(user)
        db.add(CreatorProfile(user_id=user.id, display_name=user.name))
        db.commit()
        from .services import ledger
        ledger.grant_promo(db, user.id, FREE_TRIAL_CREDITS * MICRO, f"trial:{user.id}")
    _cache[token] = (time.time() + 300, user.id)
    return user


def user_for_token(db: DB, token: str) -> User | None:
    if not token:
        return None
    user = db.scalar(select(User).where(User.api_token == token))
    if user is None and supabase_configured() and token.count(".") == 2:  # looks like a JWT
        user = _supabase_user(db, token)
    return user if user and user.status == "active" else None


def current_user(authorization: str = Header("", description="Bearer <token>: demo tok_sam / tok_riya / tok_admin, "
                                                             "or a Supabase access token when configured"),
                 db: DB = Depends(get_db)) -> User:
    user = user_for_token(db, authorization.removeprefix("Bearer ").strip())
    if user is None:
        raise HTTPException(401, detail="invalid or missing user token")
    return user


def require_role(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if role not in user.roles:
            raise HTTPException(403, detail=f"{role} role required")
        return user
    return dep
