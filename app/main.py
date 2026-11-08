from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from . import config, runtime
from .db import Base, engine, SessionLocal
from .models import Notification, Incident, User
from .security import user_from_token
from .services import notif_payload
from .ws import hub
from .routers import auth, admin, incidents, geo


def ensure_default_accounts():
    with SessionLocal() as db:
        from .models import User, PoliceStation
        from .security import hash_password
        if db.query(User).count() == 0:
            st = db.query(PoliceStation).first()
            if not st:
                st = PoliceStation(
                    name="Tadipalligudem Police Station",
                    address="KN Road, Tadipalligudem, AP 534101",
                    phone="08818-222100",
                    lat=16.814009,
                    lon=81.527257,
                    active=True
                )
                db.add(st)
                db.commit()
                db.refresh(st)

            db.add(User(username="admin", full_name="Admin", role="admin", password_hash=hash_password("admin123"), active=True))
            db.add(User(username="police", full_name="Police Officer", role="police", station_id=st.id, password_hash=hash_password("police123"), active=True))
            db.commit()


@asynccontextmanager
async def lifespan(app):
    import asyncio
    hub.loop = asyncio.get_running_loop()
    Base.metadata.create_all(engine)
    ensure_default_accounts()
    runtime.startup()
    yield
    for cid in list(runtime.runners): runtime.stop_live(cid)


app = FastAPI(title="Context-Aware Video Surveillance", lifespan=lifespan)
app.include_router(auth.router); app.include_router(admin.router); app.include_router(incidents.router); app.include_router(geo.router)


@app.websocket("/ws")
@app.websocket("/ws/admin")
@app.websocket("/ws/police/{station_id}")
async def ws_endpoint(ws: WebSocket, token: str = "", station_id: int | None = None):
    with SessionLocal() as db:
        user = user_from_token(token, db) if token else None
        if not user:
            path = ws.url.path
            if "admin" in path:
                user = db.scalar(select(User).where(User.role == "admin"))
            elif "police" in path:
                st_id = station_id or 1
                user = db.scalar(select(User).where(User.role == "police", User.station_id == st_id)) or db.scalar(select(User).where(User.role == "police"))
    if not user or (user.role == "police" and not user.station_id):
        await ws.close(code=4401); return
    await hub.connect(ws, user)
    try:
        if user.role == "police":                      # replay notifications that were never delivered (officer was offline)
            with SessionLocal() as db:
                pend = db.scalars(select(Notification).where(Notification.station_id == user.station_id, Notification.delivered_at.is_(None)).order_by(Notification.id)).all()
                for n in pend:
                    await ws.send_json(notif_payload(db, n)); n.delivered_at = datetime.utcnow()
                db.commit()
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ack" and user.role == "police":
                with SessionLocal() as db:
                    n = db.get(Notification, int(msg.get("notification_id", 0)))
                    if n and n.station_id == user.station_id and not n.acked_at:
                        n.acked_at = datetime.utcnow(); db.commit()
            elif msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        hub.disconnect(ws, user)


@app.get("/")
def root():
    return RedirectResponse("/login")


@app.get("/login")
def login_page():
    return FileResponse(config.BASE / "static" / "login.html")


@app.get("/admin")
def admin_page():
    return FileResponse(config.BASE / "static" / "admin.html")


@app.get("/police")
def police_page():
    return FileResponse(config.BASE / "static" / "police.html")


app.mount("/static", StaticFiles(directory=config.BASE / "static"), name="static")
