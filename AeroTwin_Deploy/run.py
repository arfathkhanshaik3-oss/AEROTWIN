#!/usr/bin/env python3
"""Start AeroTwin:  python run.py     ->  dashboard at http://localhost:8000"""
import sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
import uvicorn
from aerotwin.config import settings

if __name__ == "__main__":
    print(f"\n  AeroTwin  ->  http://localhost:{settings.port}   (source={settings.source})\n")
    uvicorn.run("aerotwin.server:app", host=settings.host, port=settings.port, log_level="info")
