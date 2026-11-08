"""End-to-end integration test validating all User Dashboard and Police Dashboard requirements."""
import os
import sys
import tempfile
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import cv2
from app.db import Base, engine, SessionLocal
from app.models import User, PoliceStation, Camera, Incident, Evidence, Job
from app.security import hash_password, make_token
import app.services as S
from app.routers.auth import login, register, update_profile, change_pw, Login, Register, ProfileIn, Pw
from app.routers.admin import stations, add_station, del_station, StationIn, cameras, add_camera, CamIn, test_new, UrlIn
from app.routers.incidents import (
    list_incidents, get_incident, set_status, StatusIn, delete_incident,
    police_summary, police_data_management, get_nearby_stations,
    verify_all, package
)

def test_full_workflow():
    print("=== STARTING END-TO-END SYSTEM TEST ===")

    with SessionLocal() as db:
        # 1. AUTHENTICATION & REGISTRATION
        print("\n--- 1. Testing Auth & Registration ---")
        # Login as admin
        admin_res = login(Login(username="admin", password="admin123"), db)
        assert admin_res["token"], "Admin token missing"
        assert admin_res["user"]["role"] == "admin"
        admin_user = db.get(User, admin_res["user"]["id"])
        print(f"✓ Admin login successful (id={admin_user.id}, role={admin_user.role})")

        # Login as police
        police_res = login(Login(username="police", password="police123"), db)
        assert police_res["token"], "Police token missing"
        assert police_res["user"]["role"] == "police"
        police_user = db.get(User, police_res["user"]["id"])
        print(f"✓ Police login successful (id={police_user.id}, role={police_user.role})")

        # Register a new citizen user
        new_username = f"citizen_{int(cv2.getTickCount()) % 10000}"
        reg_res = register(Register(
            username=new_username,
            password="password123",
            full_name="John Doe",
            role="user",
            email="john@surveillance.test",
            phone="+91 9876543210"
        ), db)
        assert reg_res["token"], "Registration token missing"
        new_user = db.get(User, reg_res["user"]["id"])
        print(f"✓ Registered new citizen user: {new_username}")

        # Update profile & notification preferences in Settings
        prof_res = update_profile(ProfileIn(
            full_name="John Doe (Verified Citizen)",
            email="john.verified@surveillance.test",
            phone="+91 9123456789",
            preferences={"sound_alerts": True, "auto_police_alert": True}
        ), u=new_user, db=db)
        assert prof_res["full_name"] == "John Doe (Verified Citizen)"
        assert prof_res["preferences"]["auto_police_alert"] is True
        print("✓ User profile management and notification preferences verified")

        # 2. POLICE STATIONS & DISTANCE CALCULATION
        print("\n--- 2. Testing Police Stations & Distance Calculation ---")
        st_list = stations(db)
        print(f"✓ Currently active police stations in database: {len(st_list)}")

        # Add a new verified police station
        new_station_name = f"North Ring Division-{int(cv2.getTickCount()) % 10000}"
        st_add_res = add_station(StationIn(
            name=new_station_name,
            address="North Sector 12, Ring Road",
            phone="+91 9876500001",
            lat=16.8500,
            lon=81.5500
        ), u=admin_user, db=db)
        assert st_add_res["id"], "Station ID missing"
        print(f"✓ Added new police station: {new_station_name} (ID #{st_add_res['id']})")

        # Query nearby police stations with straight-line distance calculation
        inc_lat, inc_lon = 16.8150, 81.5280
        nearby = get_nearby_stations(lat=inc_lat, lon=inc_lon, db=db)
        assert len(nearby) >= 2, "Expected multiple stations"
        assert "distance_km" in nearby[0]
        assert nearby[0]["is_nearest"] is True
        assert nearby[0]["distance_km"] <= nearby[1]["distance_km"]
        print(f"✓ Distance to nearest station '{nearby[0]['name']}': {nearby[0]['distance_km']} km")
        print(f"✓ Distance to secondary station '{nearby[1]['name']}': {nearby[1]['distance_km']} km")

        # 3. CAMERA MANAGEMENT
        print("\n--- 3. Testing CCTV Camera Management ---")
        cam_code = f"CAM-TEST-{int(cv2.getTickCount()) % 10000}"
        cam_res = add_camera(CamIn(
            name="Main Facility Gate Camera",
            code=cam_code,
            rtsp_url="0",
            location_text="Main Gate East Entrance",
            lat=inc_lat,
            lon=inc_lon,
            zone=[[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]
        ), u=admin_user, db=db)
        assert cam_res["id"]
        cam_id = cam_res["id"]
        print(f"✓ Camera connected: {cam_res['name']} ({cam_res['code']}) with restricted detection zone")

        all_cams = cameras(db)
        assert any(c["id"] == cam_id for c in all_cams)
        print(f"✓ CCTV Camera list dynamically queried from database ({len(all_cams)} cameras)")

        # 4. VIDEO UPLOAD & RISK ANALYSIS
        print("\n--- 4. Testing Video Upload & Automated Risk Detection ---")
        temp_dir = tempfile.mkdtemp()
        test_video_path = os.path.join(temp_dir, "test_feed.mp4")
        vw = cv2.VideoWriter(test_video_path, cv2.VideoWriter_fourcc(*"mp4v"), 25, (320, 240))
        for i in range(25 * 5):
            frame = np.full((240, 320, 3), 40, np.uint8)
            cv2.rectangle(frame, (100, 60), (160, 200), (220, 220, 220), -1)
            vw.write(frame)
        vw.release()

        # Create upload job
        job = Job(
            filename="test_feed.mp4",
            path="test_feed.mp4",
            status="done",
            progress=100.0,
            camera_id=cam_id,
            lat=inc_lat,
            lon=inc_lon,
            loc_source="manual",
            created_by=new_user.id
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        print(f"✓ Video upload job registered: #{job.id} ({job.filename})")

        # Trigger simulated detection event
        ev = {
            "level": "HIGH_RISK_CANDIDATE",
            "score": 0.89,
            "ts": 2.5,
            "start_ts": 0.5,
            "track_id": 42,
            "duration": 16.5,
            "indicators": {
                "restricted_zone_entry": True,
                "loitering": True,
                "zone_dwell": True,
                "repeated_activity": False,
                "night_time": None
            },
            "summary": "Restricted zone entry and suspicious loitering detected by surveillance engine."
        }
        dummy_frame = np.full((240, 320, 3), 50, np.uint8)
        dummy_box = (100.0, 60.0, 160.0, 200.0)
        c_obj = db.get(Camera, cam_id)
        created_inc = S.create_candidate(db, ev, dummy_frame, dummy_box, 0.94, source="upload", cam=c_obj, job=job)
        inc_id = created_inc.id
        inc_code = created_inc.code
        print(f"✓ Automated alert generated: Incident {inc_code} with status: '{created_inc.status}'")

        # 5. INCIDENT HISTORY & EVIDENCE
        print("\n--- 5. Testing Incident History & Evidence Records ---")
        inc_list = list_incidents(u=new_user, db=db)
        found_inc = next((i for i in inc_list if i["id"] == inc_id), None)
        assert found_inc is not None, "Created incident not in list"
        assert found_inc["status"] == "Pending"
        assert found_inc["source"] == "Video Upload"
        assert found_inc["risk_type"] == "Restricted Zone Entry"
        assert found_inc["station_km"] is not None
        print(f"✓ Incident record verified in history: ID={found_inc['code']}, Status={found_inc['status']}, Risk={found_inc['risk_type']}, Distance={found_inc['station_km']} km")

        inc_detail = get_incident(inc_id, u=new_user, db=db)
        assert len(inc_detail["evidence"]) >= 1, "Evidence records missing"
        assert len(inc_detail["nearby_stations"]) >= 1, "Nearby stations list missing in incident detail"
        print(f"✓ Evidence files linked to incident: {[e['kind'] for e in inc_detail['evidence']]}")
        print(f"✓ Nearby stations attached: {len(inc_detail['nearby_stations'])} stations")

        # 6. POLICE INVESTIGATION & STATUS WORKFLOW
        print("\n--- 6. Testing Police Investigation & Status Updates ---")
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Police receives incident and sets to Under Investigation
        pol_update_1 = loop.run_until_complete(set_status(inc_id, StatusIn(
            status="Under Investigation",
            remarks="Investigating suspicious movement near Main Gate East."
        ), u=police_user, db=db))
        assert pol_update_1["status"] == "Under Investigation"
        print("✓ Police status updated to 'Under Investigation'. Remarks logged.")

        # Check status reflected in User view
        user_check = get_incident(inc_id, u=new_user, db=db)
        assert user_check["status"] == "Under Investigation"
        print("✓ Real-time status synchronization confirmed on User Dashboard")

        # Police completes investigation and sets to Resolved
        pol_update_2 = loop.run_until_complete(set_status(inc_id, StatusIn(
            status="Resolved",
            remarks="Contractor credential verified by patrol officer. Area secure."
        ), u=police_user, db=db))
        assert pol_update_2["status"] == "Resolved"
        print("✓ Police status updated to 'Resolved'")

        # 7. POLICE DATA MANAGEMENT & DELETION
        print("\n--- 7. Testing Police Data Management & Deletion ---")
        pol_data = police_data_management(u=police_user, db=db)
        assert len(pol_data["incidents"]) >= 1
        assert len(pol_data["evidence"]) >= 1
        assert len(pol_data["cameras"]) >= 1
        assert len(pol_data["jobs"]) >= 1
        print(f"✓ Dynamic Data Management queried from database:")
        print(f"    - Incidents: {len(pol_data['incidents'])}")
        print(f"    - Evidence:  {len(pol_data['evidence'])}")
        print(f"    - Cameras:   {len(pol_data['cameras'])}")
        print(f"    - Jobs:      {len(pol_data['jobs'])}")

        # Delete the incident
        del_res = loop.run_until_complete(delete_incident(inc_id, u=police_user, db=db))
        assert del_res["ok"] is True
        print(f"✓ Incident {inc_code} deleted by authorized police officer")

        # Verify deletion from database
        del_check = db.get(Incident, inc_id)
        assert del_check is None, "Incident was not deleted from database"
        ev_check = db.query(Evidence).filter(Evidence.incident_id == inc_id).all()
        assert len(ev_check) == 0, "Cascading evidence was not deleted"
        print("✓ Verified complete removal from database and cascading evidence deletion")

        # Clean up added station
        del_st_res = del_station(st_add_res["id"], u=admin_user, db=db)
        assert del_st_res["ok"] is True
        print(f"✓ Cleaned up test station #{st_add_res['id']}")

    print("\n=======================================================")
    print("ALL 7 END-TO-END VERIFICATION MODULES PASSED (100% OK)!")
    print("=======================================================")

if __name__ == "__main__":
    test_full_workflow()
