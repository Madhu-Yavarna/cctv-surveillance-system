import os
import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")

    print("=" * 70)
    print("  Context-Aware Video Surveillance Architecture")
    print("  Forensic Packaging and Geospatial Alert Routing")
    print("=" * 70)
    print(f"  Web Portal: http://{host}:{port}")
    print(f"  Central Admin:  http://{host}:{port}/admin   (admin / admin123)")
    print(f"  Police Officer: http://{host}:{port}/police  (police / police123)")
    print("=" * 70)

    uvicorn.run("app.main:app", host=host, port=port, reload=False)
