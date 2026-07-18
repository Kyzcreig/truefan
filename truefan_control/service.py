import threading
from typing import Dict, Optional

from .backend import FanBackend
from .policy import SafetyPolicy


PROFILES = {"quiet": 22, "cooling": 50, "emergency": 100}


class ControlService:
    def __init__(self, backend: FanBackend, policy: SafetyPolicy) -> None:
        self.backend = backend
        self.policy = policy
        self._lock = threading.RLock()

    @staticmethod
    def _mutation_result(requested: int, decision, readback) -> Dict[str, object]:
        return {
            "requested_duty": requested,
            "effective_duty": decision.effective_duty,
            "reason": decision.reason,
            "safety_state": decision.state,
            "mode": readback.mode,
            "readback": {
                "verified": readback.mode == "manual"
                and readback.duty_percent == decision.effective_duty,
                "duty_percent": readback.duty_percent,
                "pwm": readback.pwm,
            },
            "override_expires_at": decision.override_expires_at,
        }

    def request_duty(self, duty_percent: int, ttl_seconds: Optional[int] = None) -> Dict[str, object]:
        with self._lock:
            before = self.backend.status()
            decision = self.policy.request_override(before, duty_percent, ttl_seconds)
            readback = self.backend.set_duty_percent(decision.effective_duty)
            return self._mutation_result(duty_percent, decision, readback)

    def request_profile(self, profile: str, ttl_seconds: Optional[int] = None) -> Dict[str, object]:
        with self._lock:
            if profile not in PROFILES:
                raise ValueError("unknown_profile")
            result = self.request_duty(PROFILES[profile], ttl_seconds)
            result["profile"] = profile
            return result

    def tick(self) -> Dict[str, object]:
        with self._lock:
            before = self.backend.status()
            decision = self.policy.evaluate(before)
            readback = before
            if before.mode != "manual" or before.duty_percent != decision.effective_duty:
                readback = self.backend.set_duty_percent(decision.effective_duty)
            return {
                "backend": readback.to_dict(),
                "safety": decision.to_dict(),
            }

    def status(self) -> Dict[str, object]:
        with self._lock:
            backend_status = self.backend.status()
            decision = self.policy.evaluate(backend_status)
            return {
                "backend": backend_status.to_dict(),
                "safety": decision.to_dict(),
            }
