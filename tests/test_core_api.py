import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import control_client  # noqa: E402
import server  # noqa: E402


AGENT_DATA = {
    "backend": {
        "backend": "ast2600_ipmi",
        "mode": "manual",
        "duty_percent": 50,
        "pwm": 128,
        "fan_rpms": {"FAN1": 2200},
        "temperatures": {
            "cpu_c": 61.0,
            "board_c": 39.0,
            "drives_c": {"sda": 38.0, "sdb": 41.0, "nvme0": 49.0},
            "max_drive_c": 41.0,
            "nvme_c": 49.0,
        },
        "sensor_ok": True,
        "error": None,
    },
    "safety": {
        "state": "normal",
        "effective_duty": 50,
        "reason": "manual_override",
        "controls_locked": False,
        "override_expires_at": 2000,
    },
}


def agent_response(data=AGENT_DATA):
    return {
        "ok": True,
        "status_code": 200,
        "data": {"ok": True, "error": None, "data": data},
        "error": "",
    }


def test_status_has_legacy_top_level_fields_and_structured_contract(monkeypatch):
    monkeypatch.setattr(server, "get_agent_status", lambda: agent_response())
    monkeypatch.setattr(server, "get_profile", lambda: "cool")
    monkeypatch.setattr(server, "get_uptime", lambda: "1h 02m")
    monkeypatch.setattr(server, "get_cpu_load", lambda: "0.10 / 0.20 / 0.30")

    response = server.app.test_client().get("/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["profile"] == "cool"
    assert payload["uptime"] == "1h 02m"
    assert payload["load"] == "0.10 / 0.20 / 0.30"
    assert payload["pwm"] == 128
    assert payload["system"] == {
        "profile": "cool",
        "uptime": "1h 02m",
        "load": "0.10 / 0.20 / 0.30",
    }
    assert payload["backend"]["backend"] == "ast2600_ipmi"
    assert payload["safety"]["state"] == "normal"
    assert payload["drives"][0] == {"name": "nvme0", "temperature_c": 49.0}
    assert payload["fan_rpms"] == {"FAN1": 2200}
    assert payload["agent_available"] is True


def test_monitoring_degrades_honestly_when_agent_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        server,
        "get_agent_status",
        lambda: {"ok": False, "status_code": 0, "data": {}, "error": "connection_failed"},
    )
    monkeypatch.setattr(server, "get_sensors_data", lambda: [{"name": "cpu", "value": 44.0}])
    monkeypatch.setattr(server, "read_fan_rpms", lambda: {"fan1": 1000})

    payload = server.app.test_client().get("/status").get_json()

    assert payload["mode"] == "monitoring-only"
    assert payload["agent_available"] is False
    assert payload["pwm_control_enabled"] is False
    assert payload["agent"]["error"] == "connection_failed"
    assert payload["sensors"] == [{"name": "cpu", "value": 44.0}]
    assert payload["fan"] == {"fan1": 1000}


def test_ui_write_token_is_distinct_and_agent_token_stays_server_side(tmp_path, monkeypatch):
    ui_file = tmp_path / "ui-token"
    agent_file = tmp_path / "agent-token"
    ui_file.write_text("ui-only\n", encoding="utf-8")
    agent_file.write_text("agent-only\n", encoding="utf-8")
    monkeypatch.setenv("TRUEFAN_UI_WRITE_TOKEN_FILE", str(ui_file))
    monkeypatch.setenv("CONTROL_AGENT_TOKEN_FILE", str(agent_file))
    calls = []
    monkeypatch.setattr(
        server,
        "agent_request_control",
        lambda duty, ttl: calls.append((duty, ttl))
        or {
            "ok": True,
            "status_code": 200,
            "data": {
                "ok": True,
                "error": None,
                "data": {"requested_duty": duty, "effective_duty": duty, "readback": {"verified": True}},
            },
            "error": "",
        },
    )
    client = server.app.test_client()

    assert client.post("/api/control", json={"duty_percent": 50}).status_code == 401
    assert client.post(
        "/api/control",
        json={"duty_percent": 50},
        headers={"Authorization": "Bearer agent-only"},
    ).status_code == 403
    accepted = client.post(
        "/api/control",
        json={"duty_percent": 50, "ttl_seconds": 60},
        headers={"Authorization": "Bearer ui-only"},
    )

    assert accepted.status_code == 200
    assert accepted.get_json()["data"]["effective_duty"] == 50
    assert calls == [(50, 60)]
    assert control_client._build_headers()["Authorization"] == "Bearer agent-only"
    assert "ui-only" not in str(control_client._build_headers())


def test_legacy_pwm_maps_to_safe_percent_and_agent_409_is_preserved(tmp_path, monkeypatch):
    ui_file = tmp_path / "ui-token"
    ui_file.write_text("ui-only", encoding="utf-8")
    monkeypatch.setenv("TRUEFAN_UI_WRITE_TOKEN_FILE", str(ui_file))
    calls = []

    def locked(duty, ttl):
        calls.append((duty, ttl))
        return {
            "ok": False,
            "status_code": 409,
            "data": {
                "ok": False,
                "error": {"code": "safety_locked", "message": "Hot threshold requires 100% duty"},
                "data": None,
            },
            "error": "HTTP 409",
        }

    monkeypatch.setattr(server, "agent_request_control", locked)
    response = server.app.test_client().post(
        "/pwm/128",
        headers={"Authorization": "Bearer ui-only"},
    )

    assert calls == [(50, 300)]
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "safety_locked"
