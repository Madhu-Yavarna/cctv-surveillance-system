# Context-Aware Video Surveillance Architecture for Forensic Packaging and Geospatial Alert Routing

**College-Level Final-Year Major Project**

A complete, functional, lightweight and practical CCTV surveillance web application built strictly to the specifications of the surveillance command-center reference designs.

---

## 1. Project Workflow & Core Pipeline

```
Live CCTV (OpenCV / RTSP) / Video Upload
                  │
                  ▼
   AI Object Detection (YOLOv8n)
                  │
                  ▼
  Temporal Association (ByteTrack Tracker)
                  │
                  ▼
  Context Analysis Engine (Restricted Zone, Loitering, Speed)
                  │
                  ▼
   Risk Suppression Engine (Multi-Criteria False-Alert Reduction)
                  │
       ┌──────────┴──────────┐
  Suppressed (Low/Noise)   Qualifies
                             │
                             ▼
                   Admin Review & Confirmation
                             │
                             ▼
  Forensic Packaging (Snapshot, Clip & SHA-256 Verification)
                             │
                             ▼
  Geospatial Routing (Haversine Nearest Station Calculation)
                             │
                             ▼
  Automatic Real-Time Push to Police Dashboard (FastAPI WebSockets)
                             │
                             ▼
  Police Officer Response (Acknowledge, Respond, Resolve)
```

---

## 2. Technology Stack

- **Frontend**: HTML5, Modern CSS3 (Dark Surveillance Command Center Theme), JavaScript (ES6+), Leaflet 1.9.4 & OpenStreetMap.
- **Backend**: Python 3.13, FastAPI, SQLAlchemy ORM, Uvicorn, Jinja2 Templates.
- **Database**: SQLite (`surveillance.db`) with full relational constraints and foreign keys.
- **Computer Vision & Tracking**: OpenCV (`opencv-python`), Lightweight YOLO (`ultralytics` YOLOv8n), Genuine ByteTrack temporal tracking implementation using Kalman filtering and Hungarian linear assignment (`scipy.optimize.linear_sum_assignment`).
- **Real-Time Communication**: Authenticated FastAPI WebSockets for station-specific notification delivery.
- **Forensic Integrity**: Bitwise SHA-256 cryptographic checksum calculation and genuine recalculation verification.
- **Geospatial Processing**: Great-circle straight-line distance via Haversine formula and Browser Geolocation API integration.

---

## 3. Two Authenticated User Roles

| Role | Username | Password | Jurisdiction / Privileges |
| :--- | :--- | :--- | :--- |
| **Central Admin / CCTV Operator** | `admin` | `admin123` | Full surveillance control, live CCTV feeds, video upload & analysis, incident confirmation, geospatial dispatch, evidence verification, system configuration. |
| **Police Officer** | `police` | `police123` | Assigned to **Central Police Station (`CPS-01`)**. Receives instant real-time alerts with snapshot, short video clip, duration, exact location, acknowledge alerts, and updates operational response status. |

---

## 4. How to Run the Application

To launch the surveillance server on Windows:

```powershell
python run.py
```

Open your web browser and navigate to:
- **Main Portal / Login**: [http://127.0.0.1:8000](http://127.0.0.1:8000)
- **Central Admin Dashboard**: [http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin)
- **Police Officer Dashboard**: [http://127.0.0.1:8000/police](http://127.0.0.1:8000/police)

---

## 5. Automated Verification Test Suite

Run the end-to-end verification suite:

```powershell
python test_system.py
```

This verifies:
1. Template rendering for all roles.
2. JWT authentication and role validation.
3. Backend Role-Based Access Control (RBAC).
4. Dynamic SQLite database statistics.
5. OpenCV camera stream connection verification.
6. Incident management and filtering.
7. Genuine SHA-256 recalculation against file bytes on disk.
8. Geospatial map markers and Haversine nearest station distance.
9. Police station-specific incident routing.
10. Police response status updates synchronized back to the Admin Dashboard.
