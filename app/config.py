"""Paths and secrets. Nothing secret is hardcoded."""
import os, secrets
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CCTV_DATA", BASE / "data"))
STORE = DATA / "storage"
UPLOADS = STORE / "uploads"
EVIDENCE = STORE / "evidence"
MODEL_PATH = Path(os.environ.get("CCTV_MODEL", BASE / "models" / "yolov8n.onnx"))
DB_URL = f"sqlite:///{(DATA / 'cctv.db').as_posix()}"
MAX_UPLOAD_MB = int(os.environ.get("CCTV_MAX_UPLOAD_MB", "500"))
ALLOWED_EXT = {".mp4", ".avi", ".mov", ".mkv"}
TOKEN_HOURS = 8

for _p in (DATA, UPLOADS, EVIDENCE):
    _p.mkdir(parents=True, exist_ok=True)


def jwt_secret() -> str:
    """Env var CCTV_SECRET, else a random key generated once and kept in data/secret.key."""
    env = os.environ.get("CCTV_SECRET")
    if env:
        return env
    f = DATA / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
    return f.read_text().strip()
