import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Job, Incident, Camera, User, Evidence, PoliceStation

db = SessionLocal()
print("=== USERS ===")
for u in db.query(User).all():
    print(f"ID={u.id}, username={u.username}, role={u.role}")

print("\n=== CAMERAS ===")
for c in db.query(Camera).all():
    print(f"ID={c.id}, code={c.code}, name={c.name}, status={c.status}, lat={c.lat}, lon={c.lon}")

print("\n=== JOBS ===")
for j in db.query(Job).all():
    print(f"ID={j.id}, filename={j.filename}, path={j.path}, status={j.status}, result={j.result_json}")

print("\n=== INCIDENTS ===")
for i in db.query(Incident).all():
    print(f"ID={i.id}, code={i.code}, risk_type={i.risk_type}, risk_level={i.risk_level}, status={i.status}, lat={i.lat}, lon={i.lon}")

print("\n=== STATIONS ===")
for s in db.query(PoliceStation).all():
    print(f"ID={s.id}, name={s.name}, lat={s.lat}, lon={s.lon}, addr={s.address}")

db.close()
