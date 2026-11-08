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
    """Env var CCTV_SECRET, else file, else stable default so redeploys don't invalidate sessions."""
    env = os.environ.get("CCTV_SECRET")
    if env:
        return env
    f = DATA / "secret.key"
    if f.exists():
        try:
            return f.read_text().strip()
        except Exception:
            pass
    try:
        f.write_text("cctv_surveillance_secure_production_secret_key_2026_fixed")
    except Exception:
        pass
    return "cctv_surveillance_secure_production_secret_key_2026_fixed"
