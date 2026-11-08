"""Create admin / police station / police user. Passwords are prompted, never stored in code.
  python scripts/manage.py create-admin
  python scripts/manage.py add-station
  python scripts/manage.py create-police
  python scripts/manage.py list"""
import sys, getpass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import select
from app.db import Base, engine, SessionLocal
from app.models import User, PoliceStation
from app.security import hash_password

Base.metadata.create_all(engine)


def pw():
    while True:
        a = getpass.getpass("Password (min 8 chars): ")
        if len(a) >= 8 and a == getpass.getpass("Repeat: "): return a
        print("Too short or not matching.")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    with SessionLocal() as db:
        if cmd == "create-admin":
            name = input("Admin username: ").strip()
            db.add(User(username=name, full_name=input("Full name: "), role="admin", password_hash=hash_password(pw()))); db.commit(); print("Admin created")
        elif cmd == "add-station":
            print("Enter REAL, verified details (nothing is invented by this app).")
            db.add(PoliceStation(name=input("Station name: ").strip(), address=input("Address: "), phone=input("Phone (optional): ") or None,
                                 lat=float(input("Latitude: ")), lon=float(input("Longitude: ")))); db.commit(); print("Station added")
        elif cmd == "create-police":
            for s in db.scalars(select(PoliceStation)): print(s.id, s.name)
            sid = int(input("Station id: "))
            db.add(User(username=input("Officer username: ").strip(), full_name=input("Full name: "), role="police", station_id=sid, password_hash=hash_password(pw()))); db.commit(); print("Officer created")
        elif cmd == "list":
            for u in db.scalars(select(User)): print("user", u.id, u.username, u.role, u.station_id)
            for s in db.scalars(select(PoliceStation)): print("station", s.id, s.name, s.lat, s.lon)
        else: print(__doc__)


main()
