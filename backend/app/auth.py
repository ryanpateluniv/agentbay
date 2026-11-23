from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DB

from .db import get_db
from .models import User


def current_user(authorization: str = Header("", description="Bearer <user token>, e.g. Bearer tok_sam"),
                 db: DB = Depends(get_db)) -> User:
    token = authorization.removeprefix("Bearer ").strip()
    user = db.scalar(select(User).where(User.api_token == token)) if token else None
    if user is None or user.status != "active":
        raise HTTPException(401, detail="invalid or missing user token")
    return user


def require_role(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if role not in user.roles:
            raise HTTPException(403, detail=f"{role} role required")
        return user
    return dep
