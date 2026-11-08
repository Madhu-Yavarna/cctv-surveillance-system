import time
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..security import verify_password, hash_password, make_token, current_user
from .. import services as S

router = APIRouter(prefix="/api/auth", tags=["auth"])
_fails: dict[str, list[float]] = {}


class Login(BaseModel):
    username: str
    password: str


def user_dict(u):
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role,
            "station": {"id": u.station.id, "name": u.station.name, "lat": u.station.lat, "lon": u.station.lon} if u.station else None}


@router.post("/login")
def login(b: Login, db: Session = Depends(get_db)):
    now = time.time(); f = [t for t in _fails.get(b.username, []) if now - t < 300]
    if len(f) >= 5:
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    u = db.scalar(select(User).where(User.username == b.username))
    if not u or not u.active or not verify_password(b.password, u.password_hash):
        f.append(now); _fails[b.username] = f
        S.audit(db, u.id if u else None, "login_failed", b.username)
        raise HTTPException(401, "Invalid username or password")
    _fails.pop(b.username, None); S.audit(db, u.id, "login", u.role)
    tok = make_token(u)
    ud = user_dict(u)
    return {"token": tok, "access_token": tok, "role": u.role, "user": ud, **ud}


@router.get("/me")
def me(u: User = Depends(current_user)):
    return user_dict(u)


class ProfileUpdate(BaseModel):
    full_name: str | None = None


@router.post("/profile")
def update_profile(b: ProfileUpdate, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if b.full_name is not None and b.full_name.strip():
        u.full_name = b.full_name.strip()[:100]
        db.merge(u); db.commit(); S.audit(db, u.id, "profile_updated", u.full_name)
    return user_dict(u)


class Pw(BaseModel):
    old: str
    new: str


@router.post("/password")
def change_pw(b: Pw, u: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(b.old, u.password_hash): raise HTTPException(400, "Current password is incorrect")
    if len(b.new) < 8: raise HTTPException(400, "New password must be at least 8 characters")
    u.password_hash = hash_password(b.new); db.merge(u); db.commit(); S.audit(db, u.id, "password_changed")
    return {"ok": True}
