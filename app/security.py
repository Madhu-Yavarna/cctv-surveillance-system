import jwt
from datetime import datetime, timedelta
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from fastapi import Depends, HTTPException, Header, Query, Cookie
from sqlalchemy.orm import Session
from . import config
from .db import get_db
from .models import User

_ph = PasswordHasher()


def hash_password(p: str) -> str:
    return _ph.hash(p)


def verify_password(p: str, h: str) -> bool:
    try:
        return _ph.verify(h, p)
    except (VerifyMismatchError, InvalidHashError):
        return False


def make_token(user: User) -> str:
    exp = datetime.utcnow() + timedelta(hours=config.TOKEN_HOURS)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, config.jwt_secret(), algorithm="HS256")


def user_from_token(token: str, db: Session) -> User | None:
    try:
        data = jwt.decode(token, config.jwt_secret(), algorithms=["HS256"])
        u = db.get(User, int(data["sub"]))
        return u if u and u.active and u.role == data.get("role") else None
    except Exception:
        return None


def optional_user(authorization: str | None = Header(None), token: str | None = Query(None), surveillance_token: str | None = Cookie(None), db: Session = Depends(get_db)) -> User | None:
    t = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else (token or surveillance_token)
    return user_from_token(t, db) if t else None


def current_user(authorization: str | None = Header(None), token: str | None = Query(None), surveillance_token: str | None = Cookie(None), db: Session = Depends(get_db)) -> User:
    """Bearer header for API calls; ?token= and cookies accepted for media tags."""
    t = authorization[7:] if authorization and authorization.lower().startswith("bearer ") else (token or surveillance_token)
    u = user_from_token(t, db) if t else None
    if not u:
        raise HTTPException(401, "Not authenticated")
    return u


def require_admin(u: User = Depends(current_user)) -> User:
    if u.role != "admin":
        raise HTTPException(403, "Admin only")
    return u


def require_police(u: User = Depends(current_user)) -> User:
    if u.role != "police" or not u.station_id:
        raise HTTPException(403, "Police officer with an assigned station only")
    return u
