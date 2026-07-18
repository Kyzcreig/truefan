"""Local verification server; never imported by production code."""

import os
from pathlib import Path

import uvicorn

from truefan_control.main import create_app


class FakeService:
    def status(self):
        return {
            "backend": {
                "backend": "fake_verification",
                "mode": "manual",
                "duty_percent": 50,
                "pwm": 128,
                "fan_rpms": {"FAN1": 2000},
                "temperatures": {
                    "cpu_c": 55.0,
                    "board_c": 38.0,
                    "drives_c": {"sda": 39.0, "nvme0": 48.0},
                    "max_drive_c": 39.0,
                    "nvme_c": 48.0,
                },
                "sensor_ok": True,
                "error": None,
            },
            "safety": {
                "state": "normal",
                "effective_duty": 50,
                "reason": "manual_override",
                "controls_locked": False,
                "override_expires_at": None,
            },
        }

    def request_duty(self, duty, ttl):
        return {
            "requested_duty": duty,
            "effective_duty": duty,
            "reason": "manual_override",
            "safety_state": "normal",
            "mode": "manual",
            "readback": {"verified": True, "duty_percent": duty, "pwm": round(duty * 255 / 100)},
            "override_expires_at": 1000 + ttl,
        }

    def request_profile(self, profile, ttl):
        duties = {"quiet": 22, "cooling": 50, "emergency": 100}
        result = self.request_duty(duties[profile], ttl)
        result["profile"] = profile
        return result


token = Path(os.environ["TRUEFAN_AGENT_SECRET_FILE"]).read_text(encoding="utf-8").strip()
uvicorn.run(create_app(service=FakeService(), expected_token=token), host="0.0.0.0", port=5088)
