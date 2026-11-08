from datetime import datetime
from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def now():
    return datetime.utcnow()


class PoliceStation(Base):
    __tablename__ = "police_stations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    address: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    full_name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(10))            # "admin" | "police"
    station_id: Mapped[int | None] = mapped_column(ForeignKey("police_stations.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    station = relationship("PoliceStation")


class Camera(Base):
    __tablename__ = "cameras"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)       # e.g. CAM-001
    name: Mapped[str] = mapped_column(String(120))
    rtsp_url: Mapped[str] = mapped_column(String(500))
    location_text: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    zone_json: Mapped[str | None] = mapped_column(Text)               # normalised restricted-zone polygon
    status: Mapped[str] = mapped_column(String(20), default="offline")  # offline | online (only after verified)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(String(255))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500))                   # relative to storage/uploads
    filesize: Mapped[int | None] = mapped_column(Integer)
    format: Mapped[str | None] = mapped_column(String(20))
    duration: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="uploaded")  # uploaded|queued|running|done|failed|cancelled
    progress: Mapped[float] = mapped_column(Float, default=0)
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"))  # supplies the restricted zone
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    loc_source: Mapped[str] = mapped_column(String(20), default="unknown")   # video_metadata|manual|unknown
    analysis_result: Mapped[str | None] = mapped_column(String(50))          # "Risk Detected", "No Risk Detected", "Uncertain", "Processing Failed"
    category: Mapped[str | None] = mapped_column(String(80))                 # e.g. "Fighting and violence", "Theft and stealing", etc.
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id", ondelete="SET NULL"))
    result_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    incident = relationship("Incident", foreign_keys=[incident_id])


class Incident(Base):
    __tablename__ = "incidents"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)       # INC-2026-001
    camera_id: Mapped[int | None] = mapped_column(ForeignKey("cameras.id"))
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"))
    source: Mapped[str] = mapped_column(String(10))                  # live | upload
    detected_at: Mapped[datetime] = mapped_column(DateTime)          # wall time of detection (upload: time of analysis)
    video_ts: Mapped[float | None] = mapped_column(Float)            # seconds into the uploaded video
    track_id: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str] = mapped_column(String(25))
    risk_score: Mapped[float] = mapped_column(Float, default=0)
    duration: Mapped[float] = mapped_column(Float, default=0)
    category: Mapped[str | None] = mapped_column(String(80))
    analysis_result: Mapped[str | None] = mapped_column(String(50))
    indicators_json: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # CANDIDATE -> (admin) REJECTED | NEW -> ACKNOWLEDGED -> RESPONDING -> RESOLVED
    status: Mapped[str] = mapped_column(String(15), default="CANDIDATE")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    location_text: Mapped[str | None] = mapped_column(String(255))
    loc_source: Mapped[str] = mapped_column(String(20), default="unknown")  # camera|video_metadata|manual|unknown
    station_id: Mapped[int | None] = mapped_column(ForeignKey("police_stations.id"))
    station_km: Mapped[float | None] = mapped_column(Float)          # straight-line distance
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
    remarks: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    camera = relationship("Camera"); station = relationship("PoliceStation"); job = relationship("Job", foreign_keys=[job_id])
    evidence = relationship("Evidence", back_populates="incident", cascade="all, delete-orphan")


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    kind: Mapped[str] = mapped_column(String(15))                    # original | clip | snapshot | manifest
    rel_path: Mapped[str] = mapped_column(String(300))               # relative to storage/evidence
    sha256: Mapped[str | None] = mapped_column(String(64))           # set when the Admin confirms
    size: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    verify_result: Mapped[str | None] = mapped_column(String(10))    # None = never verified | match | mismatch | missing
    incident = relationship("Incident", back_populates="evidence")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("police_stations.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime)
    __table_args__ = (UniqueConstraint("incident_id", "station_id"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=now)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[str | None] = mapped_column(Text)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
