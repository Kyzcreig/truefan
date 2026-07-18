import copy
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
        self._snapshot_lock = threading.Lock()
        self._last_status: Optional[Dict[str, object]] = None
        self._last_status_error: Optional[str] = None

    def _publish_status(self, payload: Dict[str, object]) -> Dict[str, object]:
        with self._snapshot_lock:
            self._last_status = copy.deepcopy(payload)
            self._last_status_error = None
            return copy.deepcopy(self._last_status)

    def _publish_status_error(self, code: str) -> None:
        with self._snapshot_lock:
            self._last_status_error = code

    def _cached_status(self) -> Optional[Dict[str, object]]:
        with self._snapshot_lock:
            if self._last_status_error is not None:
                raise RuntimeError(self._last_status_error)
            return copy.deepcopy(self._last_status)

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
            result = self._mutation_result(duty_percent, decision, readback)
            self._publish_status(
                {
                    "backend": readback.to_dict(),
                    "safety": decision.to_dict(),
                }
            )
            return result

    def request_profile(self, profile: str, ttl_seconds: Optional[int] = None) -> Dict[str, object]:
        with self._lock:
            if profile not in PROFILES:
                raise ValueError("unknown_profile")
            result = self.request_duty(PROFILES[profile], ttl_seconds)
            result["profile"] = profile
            return result

    def tick(self) -> Dict[str, object]:
        try:
            with self._lock:
                before = self.backend.status()
                decision = self.policy.evaluate(before)
                readback = before
                if before.mode != "manual" or before.duty_percent != decision.effective_duty:
                    readback = self.backend.set_duty_percent(decision.effective_duty)
                return self._publish_status(
                    {
                        "backend": readback.to_dict(),
                        "safety": decision.to_dict(),
                    }
                )
        except Exception:
            self._publish_status_error("status_refresh_failed")
            raise

    def status(self) -> Dict[str, object]:
        cached = self._cached_status()
        if cached is not None:
            return cached
        return self.tick()
