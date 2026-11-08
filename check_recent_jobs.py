from app.db import SessionLocal
from app.models import Job, Incident
import json

db = SessionLocal()
print("RECENT JOBS IN DB:")
for j in db.query(Job).order_by(Job.id.desc()).limit(6):
    print(f"Job {j.id}: filename='{j.filename}', path='{j.path}', status='{j.status}', category='{j.category}', result='{j.analysis_result}', incident_id={j.incident_id}")
    if j.result_json:
        try:
            res = json.loads(j.result_json)
            print(f"   Indicators: {res.get('indicators')}")
            print(f"   Summary: {res.get('summary')}")
        except Exception:
            pass
