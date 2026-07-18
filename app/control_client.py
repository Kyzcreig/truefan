import json
import logging
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


LOGGER = logging.getLogger(__name__)
BASE_URL = "http://127.0.0.1:5088"
TIMEOUT_SECONDS = 2.0


def _result(ok: bool, status_code: int, data: Optional[Dict[str, Any]], error: str) -> Dict[str, Any]:
    return {"ok": ok, "status_code": status_code, "data": data or {}, "error": error}


def _base_url() -> str:
    return os.getenv("CONTROL_AGENT_URL", BASE_URL).strip().rstrip("/") or BASE_URL


def _read_agent_token() -> str:
    path = os.getenv("CONTROL_AGENT_TOKEN_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        LOGGER.error("Control-agent token file is unavailable")
        return ""


def _build_headers() -> Dict[str, str]:
    token = _read_agent_token()
    if not token:
        return {"Content-Type": "application/json"}
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _request(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    url = f"{_base_url()}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url=url, data=body, headers=_build_headers(), method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8").strip()
            parsed = json.loads(raw) if raw else {}
            return _result(True, response.status, parsed, "")
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8").strip()
            parsed = json.loads(raw) if raw else {}
        except Exception:
            parsed = {}
        LOGGER.error("Control agent returned HTTP %s", exc.code)
        return _result(False, exc.code, parsed, f"HTTP {exc.code}")
    except (urllib.error.URLError, socket.timeout, ConnectionError):
        LOGGER.error("Control agent connection failed")
        return _result(False, 0, {}, "connection_failed")
    except Exception:
        LOGGER.error("Unexpected control client error")
        return _result(False, 0, {}, "request_failed")


def get_status() -> Dict[str, Any]:
    return _request("GET", "/status")


def request_control(duty_percent: int, ttl_seconds: int = 300) -> Dict[str, Any]:
    return _request(
        "POST",
        "/control",
        {"duty_percent": duty_percent, "ttl_seconds": ttl_seconds},
    )


def request_profile(profile: str, ttl_seconds: int = 300) -> Dict[str, Any]:
    return _request("POST", f"/profile/{profile}", {"ttl_seconds": ttl_seconds})


def set_pwm(pwm: int) -> Dict[str, Any]:
    return _request("POST", "/set_pwm", {"pwm": pwm, "ttl_seconds": 300})


def get_agent_health(force: bool = False, max_age_seconds: float = 0) -> Dict[str, Any]:
    del force, max_age_seconds
    response = get_status()
    return {
        "online": bool(response.get("ok")),
        "status_code": int(response.get("status_code") or 0),
        "error": response.get("error") or "",
        "age_seconds": 0.0,
    }


def refresh_agent_health(timeout: float = TIMEOUT_SECONDS) -> Dict[str, Any]:
    del timeout
    return get_agent_health()
