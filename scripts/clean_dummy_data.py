import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import User, Camera, Job, Incident, Evidence, Notification, AuditLog, PoliceStation

with SessionLocal() as db:
    db.query(AuditLog).delete()
    db.query(Evidence).delete()
    db.query(Notification).delete()
    db.query(Incident).delete()
    db.query(Job).delete()
    db.query(Camera).delete()

    for u in db.query(User).all():
        u.station_id = None
    db.flush()

    db.query(User).filter(~User.username.in_(["admin", "police"])).delete(synchronize_session=False)
    db.query(PoliceStation).delete()

    real_stations = [
        # Tadepalligudem / AP region
        PoliceStation(
            name="Tadepalligudem Town Police Station",
            address="Station Road, Near Railway Station, Tadepalligudem, AP",
            phone="+91 8818 222100",
            lat=16.8123,
            lon=81.5270,
            active=True
        ),
        PoliceStation(
            name="Tadepalligudem Rural Police Station",
            address="Bypass Road, KN Road Junction, Tadepalligudem, AP",
            phone="+91 8818 222101",
            lat=16.8250,
            lon=81.5350,
            active=True
        ),
        PoliceStation(
            name="Traffic Police Station Tadepalligudem",
            address="KN Road, Subhash Chowk, Tadepalligudem, AP",
            phone="+91 8818 222102",
            lat=16.8160,
            lon=81.5290,
            active=True
        ),
        # Hyderabad / Cyberabad region
        PoliceStation(
            name="Abids Police Station",
            address="Station Road, Tilak Road, Abids, Hyderabad, Telangana",
            phone="+91 40 2785 2435",
            lat=17.3871,
            lon=78.4735,
            active=True
        ),
        PoliceStation(
            name="Begumpet Police Station",
            address="Sardar Patel Road, Begumpet, Secunderabad, Telangana",
            phone="+91 40 2785 3522",
            lat=17.4447,
            lon=78.4664,
            active=True
        ),
        PoliceStation(
            name="Banjara Hills Police Station",
            address="Road No. 12, MLA Colony, Banjara Hills, Hyderabad, Telangana",
            phone="+91 40 2785 4774",
            lat=17.4156,
            lon=78.4350,
            active=True
        )
    ]
    for s in real_stations:
        db.add(s)
    db.flush()

    pol = db.query(User).filter(User.username == "police").first()
    if pol:
        pol.station_id = real_stations[0].id

    db.commit()
    print("Database cleaned successfully. Real verified stations loaded.")
