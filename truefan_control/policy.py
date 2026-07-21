import json
import math
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .backend import BackendStatus


DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 900

# Single source of truth for every temperature threshold.
# SAFETY thresholds (drive/cpu hot + recovery) drive the fan policy in evaluate();
# the display-only warm/hot tiers let the dashboard colour every sensor from ONE
# authoritative place (previously duplicated as magic numbers in dashboard.js).
# Per-device ranges are intentionally distinct — a CPU tolerates far more than an HDD.
HOT_DRIVE_C = 44        # max HDD above this -> hot (fans forced 100%)
HOT_CPU_C = 70          # cpu above this -> hot
RECOVER_DRIVE_C = 40    # both must fall to/below these to clear a hot incident
RECOVER_CPU_C = 60

# {sensor_key: {"warm": x, "hot": y}} — consumed by /status and the dashboard.
# drive/cpu "hot" mirror the safety numbers above (kept in sync via the refs below).
THRESHOLDS = {
    "max_drive_c": {"warm": 41, "hot": HOT_DRIVE_C},
    "cpu_c": {"warm": 61, "hot": HOT_CPU_C},
    "board_c": {"warm": 55, "hot": 70},
    "nvme_c": {"warm": 60, "hot": 75},
    "recover": {"drive": RECOVER_DRIVE_C, "cpu": RECOVER_CPU_C},
}


class SafetyLocked(RuntimeError):
    code = "safety_locked"

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass
class SafetyDecision:
    state: str
    effective_duty: int
    reason: str
    controls_locked: bool = False
    override_expires_at: Optional[float] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "state": self.state,
            "effective_duty": self.effective_duty,
            "reason": self.reason,
            "controls_locked": self.controls_locked,
            "override_expires_at": self.override_expires_at,
        }


class ControlStateStore:
    def __init__(self, path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock

    @staticmethod
    def default_state() -> Dict[str, object]:
        return {"version": 1, "previous_hot": False, "override": None}

    def load(self) -> Dict[str, object]:
        if not self.path.exists():
            return self.default_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {"version": 1, "previous_hot": True, "override": None}
        if not isinstance(data, dict):
            return {"version": 1, "previous_hot": True, "override": None}
        override = data.get("override")
        if not isinstance(override, dict):
            override = None
        else:
            duty = override.get("duty_percent")
            expires_at = override.get("expires_at")
            if (
                isinstance(duty, bool)
                or not isinstance(duty, int)
                or not 22 <= duty <= 100
                or isinstance(expires_at, bool)
                or not isinstance(expires_at, (int, float))
                or not math.isfinite(expires_at)
            ):
                override = None
        return {
            "version": 1,
            "previous_hot": bool(data.get("previous_hot", False)),
            "override": override,
        }

    def save(self, state: Dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".control-state.",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            with handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


class SafetyPolicy:
    def __init__(self, store: ControlStateStore, *, clock=time.time) -> None:
        self._store = store
        self._clock = clock
        self._state = store.load()
        self._lock = threading.RLock()

    def _save(self) -> None:
        self._store.save(self._state)

    def _active_override(self, now: float) -> Optional[Dict[str, object]]:
        override = self._state.get("override")
        if not isinstance(override, dict):
            return None
        if float(override["expires_at"]) <= now:
            self._state["override"] = None
            self._save()
            return None
        return override

    @staticmethod
    def _required_temperatures(status: BackendStatus):
        temperatures = status.temperatures or {}
        return temperatures.get("cpu_c"), temperatures.get("max_drive_c")

    def evaluate(
        self,
        status: BackendStatus,
        *,
        requested_duty: Optional[int] = None,
        now: Optional[float] = None,
    ) -> SafetyDecision:
        with self._lock:
            current_time = self._clock() if now is None else now
            override = self._active_override(current_time)
            override_duty = int(override["duty_percent"]) if override else None
            override_expiry = float(override["expires_at"]) if override else None
            cpu_c, drive_c = self._required_temperatures(status)

            if not status.sensor_ok or cpu_c is None or drive_c is None:
                if status.duty_percent is None:
                    effective = 100
                    locked = False
                else:
                    candidate = requested_duty if requested_duty is not None else override_duty
                    effective = status.duty_percent if candidate is None else max(status.duty_percent, candidate)
                    locked = requested_duty is not None and requested_duty < status.duty_percent
                return SafetyDecision(
                    "sensor-failure",
                    effective,
                    "sensor_failure_fail_closed",
                    controls_locked=locked or requested_duty is None,
                    override_expires_at=override_expiry,
                )

            cpu_c = float(cpu_c)
            drive_c = float(drive_c)
            if drive_c > HOT_DRIVE_C or cpu_c > HOT_CPU_C:
                if not self._state.get("previous_hot"):
                    self._state["previous_hot"] = True
                    self._save()
                return SafetyDecision(
                    "hot",
                    100,
                    "hot_threshold",
                    controls_locked=requested_duty is None or requested_duty < 100,
                    override_expires_at=override_expiry,
                )

            recovered = drive_c <= RECOVER_DRIVE_C and cpu_c <= RECOVER_CPU_C
            if self._state.get("previous_hot") and not recovered:
                candidate = requested_duty if requested_duty is not None else override_duty
                return SafetyDecision(
                    "cooling",
                    max(50, candidate or 50),
                    "cooling_band",
                    override_expires_at=override_expiry,
                )

            just_recovered = bool(self._state.get("previous_hot")) and recovered
            if just_recovered:
                self._state["previous_hot"] = False
                self._save()

            candidate = requested_duty if requested_duty is not None else override_duty
            return SafetyDecision(
                "normal",
                candidate if candidate is not None else 22,
                "manual_override" if candidate is not None else ("recovered" if just_recovered else "normal_policy"),
                override_expires_at=override_expiry,
            )

    def request_override(
        self,
        status: BackendStatus,
        duty_percent: int,
        ttl_seconds: Optional[int] = None,
    ) -> SafetyDecision:
        if isinstance(duty_percent, bool) or not isinstance(duty_percent, int) or not 22 <= duty_percent <= 100:
            raise ValueError("duty_out_of_range")
        ttl = DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= MAX_TTL_SECONDS:
            raise ValueError("ttl_out_of_range")
        with self._lock:
            now = self._clock()
            decision = self.evaluate(status, requested_duty=duty_percent, now=now)
            if decision.state == "hot" and duty_percent < 100:
                raise SafetyLocked("Hot threshold requires 100% duty")
            if (
                decision.state == "sensor-failure"
                and status.duty_percent is not None
                and duty_percent < status.duty_percent
            ):
                raise SafetyLocked("Sensor failure forbids lowering known fan duty")
            expires_at = now + ttl
            self._state["override"] = {
                "duty_percent": duty_percent,
                "expires_at": expires_at,
            }
            self._save()
            decision.override_expires_at = expires_at
            return decision
