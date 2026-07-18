from fastapi.testclient import TestClient

from truefan_control.main import create_app
from truefan_control.policy import SafetyLocked


class FakeService:
    def __init__(self):
        self.calls = []

    def status(self):
        return {
            "backend": {"backend": "fake", "mode": "manual", "duty_percent": 50},
            "safety": {"state": "normal", "effective_duty": 50},
        }

    def request_duty(self, duty, ttl):
        self.calls.append((duty, ttl))
        if duty == 22:
            raise SafetyLocked("Hot threshold requires 100% duty")
        return {"requested_duty": duty, "effective_duty": duty, "readback": {"verified": True}}

    def request_profile(self, profile, ttl):
        self.calls.append((profile, ttl))
        return {"profile": profile, "requested_duty": 50, "effective_duty": 50}


def test_agent_token_is_required_for_status_and_control():
    service = FakeService()
    client = TestClient(create_app(service=service, expected_token="agent-only"))

    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"Authorization": "Bearer wrong"}).status_code == 403
    ok = client.get("/status", headers={"Authorization": "Bearer agent-only"})
    assert ok.status_code == 200
    assert ok.json()["data"]["backend"]["backend"] == "fake"

    assert client.post("/control", json={"duty_percent": 50}).status_code == 401
    changed = client.post(
        "/control",
        json={"duty_percent": 50, "ttl_seconds": 300},
        headers={"Authorization": "Bearer agent-only"},
    )
    assert changed.status_code == 200
    assert changed.json()["data"]["effective_duty"] == 50


def test_agent_safety_lock_is_structured_409():
    client = TestClient(create_app(service=FakeService(), expected_token="agent-only"))

    response = client.post(
        "/control",
        json={"duty_percent": 22, "ttl_seconds": 300},
        headers={"Authorization": "Bearer agent-only"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "ok": False,
        "error": {"code": "safety_locked", "message": "Hot threshold requires 100% duty"},
        "data": None,
    }


def test_agent_compat_pwm_converts_to_percent_and_profiles_are_one_shot():
    service = FakeService()
    client = TestClient(create_app(service=service, expected_token="agent-only"))
    headers = {"Authorization": "Bearer agent-only"}

    pwm = client.post("/set_pwm", json={"pwm": 128, "ttl_seconds": 60}, headers=headers)
    profile = client.post("/profile/cooling", json={"ttl_seconds": 120}, headers=headers)

    assert pwm.status_code == 200
    assert service.calls[0] == (50, 60)
    assert profile.status_code == 200
    assert service.calls[1] == ("cooling", 120)
