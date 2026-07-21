import copy
import logging
import os
import secrets
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from control import load_profile as control_load_profile
from control_client import get_status as get_agent_status
from control_client import request_control as agent_request_control
from control_client import request_profile as agent_request_profile
from sensors import get_smart_capabilities, read_fan_rpms
from temperature_sources import get_temperature_sources


app = Flask(__name__, static_folder="static", template_folder="templates")
LOGGER = logging.getLogger(__name__)

DEFAULT_SENSORS = [
    {"name": "cpu", "value": 0.0},
    {"name": "nvme", "value": 0.0},
    {"name": "hdd", "value": 0.0},
]
DEFAULT_SYSTEM = {"profile": "unknown", "uptime": "0h 0m", "load": "0.00 / 0.00 / 0.00"}


def _default_sensors():
    return copy.deepcopy(DEFAULT_SENSORS)


def _default_status():
    system = dict(DEFAULT_SYSTEM)
    return {
        **system,
        "pwm": None,
        "mode": "monitoring-only",
        "agent_available": False,
        "pwm_control_enabled": False,
        "agent": {
            "online": False,
            "status_code": 0,
            "error": "uninitialized",
            "last_refresh_epoch": None,
        },
        "sensors": _default_sensors(),
        "capabilities": {"smart_available": True},
        "fan": {},
        "fan_rpms": {},
        "temperatures": {},
        "drives": [],
        "backend": {
            "backend": "unknown",
            "mode": "unknown",
            "duty_percent": None,
            "pwm": None,
            "fan_rpms": {},
            "temperatures": {},
            "sensor_ok": False,
            "error": {"code": "agent_unavailable", "message": "Control agent is unavailable"},
        },
        "safety": {
            "state": "unknown",
            "effective_duty": None,
            "reason": "control_agent_unavailable",
            "controls_locked": True,
            "override_expires_at": None,
        },
        "control": {
            "min_duty_percent": 22,
            "max_duty_percent": 100,
            "default_ttl_seconds": 300,
            "max_ttl_seconds": 900,
        },
        "system": system,
    }


def _agent_payload(response):
    if not response.get("ok"):
        return None
    envelope = response.get("data") or {}
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        return None
    data = envelope.get("data")
    return data if isinstance(data, dict) else None


def _legacy_sensors(temperatures):
    mapping = (
        ("cpu", temperatures.get("cpu_c")),
        ("board", temperatures.get("board_c")),
        ("hdd", temperatures.get("max_drive_c")),
        ("nvme", temperatures.get("nvme_c")),
    )
    return [{"name": name, "value": value} for name, value in mapping if value is not None]


def _build_status_payload() -> dict:
    payload = _default_status()
    system = {
        "profile": get_profile() or DEFAULT_SYSTEM["profile"],
        "uptime": get_uptime() or DEFAULT_SYSTEM["uptime"],
        "load": get_cpu_load() or DEFAULT_SYSTEM["load"],
    }
    payload.update(system)
    payload["system"] = system
    payload["capabilities"] = get_smart_capabilities()

    response = get_agent_status()
    agent_data = _agent_payload(response)
    payload["agent"] = {
        "online": agent_data is not None,
        "status_code": int(response.get("status_code") or 0),
        "error": "" if agent_data is not None else response.get("error") or "invalid_response",
        "last_refresh_epoch": time.time(),
    }
    if agent_data is None:
        payload["sensors"] = get_sensors_data() or _default_sensors()
        payload["fan"] = read_fan_rpms()
        payload["fan_rpms"] = dict(payload["fan"])
        return payload

    backend = agent_data.get("backend") or {}
    safety = agent_data.get("safety") or {}
    temperatures = backend.get("temperatures") or {}
    fan_rpms = backend.get("fan_rpms") or {}
    drives = temperatures.get("drives_c") or {}
    thresholds = agent_data.get("thresholds") or {}

    payload.update(
        {
            "mode": "full-control",
            "agent_available": True,
            "pwm_control_enabled": True,
            "pwm": backend.get("pwm"),
            "backend": backend,
            "safety": safety,
            "temperatures": temperatures,
            "thresholds": thresholds,
            "drives": [
                {"name": name, "temperature_c": value}
                for name, value in sorted(drives.items(), key=lambda item: item[1], reverse=True)
            ],
            "fan": fan_rpms,
            "fan_rpms": fan_rpms,
            "sensors": _legacy_sensors(temperatures),
        }
    )
    return payload


def _read_secret_file(variable: str):
    path = os.getenv(variable, "").strip()
    if not path:
        return None
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _require_write_access():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return False, "missing_token", "Missing Bearer token", 401
    expected = _read_secret_file("TRUEFAN_UI_WRITE_TOKEN_FILE")
    if not expected:
        return False, "write_auth_unavailable", "UI write authorization is not configured", 503
    presented = header[len("Bearer ") :].strip()
    if not secrets.compare_digest(presented, expected):
        return False, "invalid_token", "Invalid token", 403
    return True, "", "", 200


def _api_result(ok: bool, error=None, data=None, status_code: int = 200):
    return jsonify({"ok": bool(ok), "error": error, "data": data if ok else None}), status_code


def _error(code: str, message: str):
    return {"code": code, "message": message}


def _proxy_agent(response):
    status_code = int(response.get("status_code") or 0)
    envelope = response.get("data") or {}
    if response.get("ok") and isinstance(envelope, dict) and envelope.get("ok") is True:
        return _api_result(True, None, envelope.get("data"), status_code or 200)
    if isinstance(envelope, dict) and isinstance(envelope.get("error"), dict):
        agent_error = envelope["error"]
        return _api_result(
            False,
            _error(agent_error.get("code", "agent_error"), agent_error.get("message", "Control request failed")),
            None,
            status_code or 503,
        )
    return _api_result(
        False,
        _error("control_agent_unavailable", "Control agent unavailable; monitoring-only mode"),
        None,
        status_code or 503,
    )


def _validated_control_body():
    body = request.get_json(silent=True) or {}
    duty = body.get("duty_percent")
    ttl = body.get("ttl_seconds", 300)
    if isinstance(duty, bool) or not isinstance(duty, int) or not 22 <= duty <= 100:
        raise ValueError("duty_percent must be an integer from 22 to 100")
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 900:
        raise ValueError("ttl_seconds must be an integer from 1 to 900")
    return duty, ttl


def get_profile():
    try:
        profile = control_load_profile()
        return profile.lower() if profile else DEFAULT_SYSTEM["profile"]
    except Exception:
        LOGGER.error("Failed to read profile")
        return DEFAULT_SYSTEM["profile"]


def get_sensors_data():
    try:
        sensors = get_temperature_sources(include_hdd=True)
        return sensors or _default_sensors()
    except Exception:
        LOGGER.error("Failed to read sensors; using defaults")
        return _default_sensors()


def get_uptime():
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            seconds = float(handle.readline().split()[0])
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    except Exception:
        return DEFAULT_SYSTEM["uptime"]


def get_cpu_load():
    try:
        load1, load5, load15 = os.getloadavg()
        return f"{load1:.2f} / {load5:.2f} / {load15:.2f}"
    except Exception:
        return DEFAULT_SYSTEM["load"]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api")
def api_index():
    return jsonify(
        {
            "status": "ok",
            "message": "TrueFan API",
            "endpoints": [
                "/sensors",
                "/status",
                "/api/control",
                "/api/profile/<profile>",
                "/pwm/<value>",
                "/set/<profile>",
            ],
        }
    )


@app.route("/sensors")
def sensors():
    return jsonify(get_sensors_data())


@app.route("/api/control", methods=["POST"])
def api_control():
    allowed, code, message, status_code = _require_write_access()
    if not allowed:
        return _api_result(False, _error(code, message), None, status_code)
    try:
        duty, ttl = _validated_control_body()
    except ValueError as exc:
        return _api_result(False, _error("invalid_request", str(exc)), None, 400)
    return _proxy_agent(agent_request_control(duty, ttl))


@app.route("/api/profile/<profile>", methods=["POST"])
def api_profile(profile):
    allowed, code, message, status_code = _require_write_access()
    if not allowed:
        return _api_result(False, _error(code, message), None, status_code)
    if profile not in {"quiet", "cooling", "emergency"}:
        return _api_result(False, _error("unknown_profile", "Unknown profile"), None, 400)
    body = request.get_json(silent=True) or {}
    ttl = body.get("ttl_seconds", 300)
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 900:
        return _api_result(False, _error("invalid_request", "ttl_seconds must be an integer from 1 to 900"), None, 400)
    return _proxy_agent(agent_request_profile(profile, ttl))


@app.route("/pwm/<value>", methods=["POST"])
def set_pwm(value):
    allowed, code, message, status_code = _require_write_access()
    if not allowed:
        return _api_result(False, _error(code, message), None, status_code)
    try:
        raw = int(value)
        if not 0 <= raw <= 255:
            raise ValueError
    except ValueError:
        return _api_result(False, _error("invalid_request", "PWM must be from 0 to 255"), None, 400)
    percent = max(22, min(100, round(raw * 100 / 255)))
    return _proxy_agent(agent_request_control(percent, 300))


@app.route("/set/<profile>", methods=["POST"])
def set_profile(profile):
    aliases = {"quiet": "quiet", "cool": "cooling", "cooling": "cooling", "aggressive": "emergency", "emergency": "emergency"}
    mapped = aliases.get(profile.lower())
    if mapped is None:
        return _api_result(False, _error("unknown_profile", "Unknown profile"), None, 400)
    return api_profile(mapped)


@app.route("/restart-container", methods=["POST"])
@app.route("/shutdown-container", methods=["POST"])
def disabled_host_operation():
    allowed, code, message, status_code = _require_write_access()
    if not allowed:
        return _api_result(False, _error(code, message), None, status_code)
    return _api_result(False, _error("operation_disabled", "Host operations are disabled"), None, 403)


@app.route("/status")
def status():
    try:
        return jsonify(_build_status_payload())
    except Exception:
        LOGGER.error("Unexpected /status failure; returning defaults")
        return jsonify(_default_status())


@app.errorhandler(404)
def not_found(_error_value):
    return jsonify({"status": "error", "message": "Not Found"}), 404


@app.errorhandler(405)
def method_not_allowed(_error_value):
    return jsonify({"status": "error", "message": "Method Not Allowed"}), 405


@app.errorhandler(Exception)
def handle_unexpected_error(error_value):
    if isinstance(error_value, HTTPException):
        return jsonify({"status": "error", "message": error_value.description}), error_value.code
    LOGGER.error("Unhandled server error")
    return _api_result(False, _error("internal_error", "Internal Server Error"), None, 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5002)
