import os
import subprocess
from typing import Callable, List, Optional

from .backend import BackendStatus, FanBackend, empty_temperatures


CHANNEL_COUNT = 16
MIN_DUTY_PERCENT = 22
MAX_DUTY_PERCENT = 100


class BackendCommandError(RuntimeError):
    """A sanitized backend command failure."""


class BackendMutationError(RuntimeError):
    """A sanitized mutation verification failure."""


def clamp_percent(percent: int) -> int:
    return max(MIN_DUTY_PERCENT, min(MAX_DUTY_PERCENT, int(percent)))


def percent_to_pwm(percent: int) -> int:
    return round(clamp_percent(percent) * 255 / 100)


def pwm_to_percent(pwm: int) -> int:
    bounded = max(0, min(255, int(pwm)))
    return clamp_percent(round(bounded * 100 / 255))


class Ast2600Ipmi:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        runner: Callable = subprocess.run,
        timeout_seconds: float = 5.0,
    ) -> None:
        self._base_argv = [
            "ipmitool",
            "-I",
            "lanplus",
            "-H",
            host,
            "-U",
            username,
            "-E",
        ]
        self._password = password
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def _raw(self, *args: str) -> str:
        argv = self._base_argv + ["raw", *args]
        child_env = os.environ.copy()
        child_env["IPMI_PASSWORD"] = self._password
        try:
            completed = self._runner(
                argv,
                shell=False,
                env=child_env,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackendCommandError("ipmi_execution_failed") from exc
        if completed.returncode != 0:
            raise BackendCommandError("ipmi_command_failed")
        return completed.stdout or ""

    @staticmethod
    def _parse_bytes(raw: str) -> List[int]:
        try:
            values = [int(token.removeprefix("0x"), 16) for token in raw.split()]
        except ValueError as exc:
            raise BackendCommandError("ipmi_invalid_response") from exc
        if not values:
            raise BackendCommandError("ipmi_empty_response")
        return values

    def read_mode(self) -> str:
        values = self._parse_bytes(self._raw("0x3a", "0xd0", "0x12"))
        if all(value == 0x02 for value in values):
            return "manual"
        if all(value == 0x00 for value in values):
            return "auto"
        return "unknown"

    def read_duty_percent(self) -> Optional[int]:
        values = self._parse_bytes(self._raw("0x3a", "0xd0", "0x0f"))
        if any(value < 0 or value > 100 for value in values):
            raise BackendCommandError("ipmi_invalid_duty")
        return values[0] if all(value == values[0] for value in values) else None

    def _read_status(self) -> BackendStatus:
        mode = self.read_mode()
        duty = self.read_duty_percent()
        return BackendStatus(
            backend="ast2600_ipmi",
            mode=mode,
            duty_percent=duty,
            pwm=percent_to_pwm(duty) if duty is not None else None,
            sensor_ok=True,
        )

    def status(self) -> BackendStatus:
        return self._read_status()

    def set_duty_percent(self, percent: int) -> BackendStatus:
        duty = clamp_percent(percent)
        self._raw("0x3a", "0xd0", "0x11", *(["0x2"] * CHANNEL_COUNT))
        duty_byte = f"0x{duty:02x}"
        self._raw("0x3a", "0xd0", "0x0e", *([duty_byte] * CHANNEL_COUNT))
        status = self._read_status()
        if status.mode != "manual" or status.duty_percent != duty:
            raise BackendMutationError("readback_mismatch")
        return status

    def set_auto(self) -> BackendStatus:
        self._raw("0x3a", "0xd0", "0x11", *(["0x0"] * CHANNEL_COUNT))
        status = self._read_status()
        if status.mode != "auto" or status.duty_percent is None:
            raise BackendMutationError("readback_mismatch")
        return status


class Ast2600Backend(FanBackend):
    def __init__(self, ipmi: Ast2600Ipmi, redfish, truenas) -> None:
        self._ipmi = ipmi
        self._redfish = redfish
        self._truenas = truenas

    def _enrich(self, status: BackendStatus) -> BackendStatus:
        temperatures = empty_temperatures()
        fan_rpms = {}
        failed = False
        try:
            redfish = self._redfish.fetch()
            temperatures["cpu_c"] = redfish.get("cpu_c")
            temperatures["board_c"] = redfish.get("board_c")
            fan_rpms = dict(redfish.get("fan_rpms") or {})
        except Exception:
            failed = True
        try:
            truenas = self._truenas.fetch()
            temperatures["drives_c"] = dict(truenas.get("drives_c") or {})
            temperatures["max_drive_c"] = truenas.get("max_drive_c")
            temperatures["nvme_c"] = truenas.get("nvme_c")
        except Exception:
            failed = True

        complete = temperatures["cpu_c"] is not None and temperatures["max_drive_c"] is not None
        status.temperatures = temperatures
        status.fan_rpms = fan_rpms
        status.sensor_ok = not failed and complete
        if not status.sensor_ok:
            status.error = {
                "code": "sensor_read_failed",
                "message": "One or more sensor sources failed",
            }
        return status

    def status(self) -> BackendStatus:
        try:
            return self._enrich(self._ipmi.status())
        except Exception:
            return BackendStatus(
                backend="ast2600_ipmi",
                sensor_ok=False,
                error={"code": "backend_read_failed", "message": "BMC status read failed"},
            )

    def set_duty_percent(self, percent: int) -> BackendStatus:
        return self._enrich(self._ipmi.set_duty_percent(percent))

    def set_auto(self) -> BackendStatus:
        return self._enrich(self._ipmi.set_auto())
