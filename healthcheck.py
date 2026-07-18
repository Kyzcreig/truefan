#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
from pathlib import Path


component = os.getenv("TRUEFAN_COMPONENT", "truefan-core")
port = 5088 if component == "truefan-control" else 5002
headers = {}
if component == "truefan-control":
    try:
        token = Path(os.environ["TRUEFAN_AGENT_SECRET_FILE"]).read_text(encoding="utf-8").strip()
    except (KeyError, OSError):
        sys.exit(1)
    headers["Authorization"] = f"Bearer {token}"

try:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/status", headers=headers)
    with urllib.request.urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))
    healthy = response.status == 200 and isinstance(payload, dict)
except Exception:
    healthy = False
sys.exit(0 if healthy else 1)
