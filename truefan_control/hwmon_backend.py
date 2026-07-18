import glob
import os
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .ast2600 import BackendMutationError, clamp_percent, percent_to_pwm, pwm_to_percent
from .backend import BackendStatus, empty_temperatures


HWMON_ROOT = "/sys/class/hwmon"
PWM_NAME = re.compile(r"^pwm[0-9]+$")


def _normalized(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _safe_pwm_path(path: str, root: str = HWMON_ROOT) -> bool:
    normalized_root = _normalized(root)
    normalized_path = _normalized(path)
    return (
        normalized_path.startswith(normalized_root + os.sep)
        and PWM_NAME.fullmatch(os.path.basename(normalized_path)) is not None
        and os.path.isfile(normalized_path)
    )


def discover_pwm_files(root: str = HWMON_ROOT) -> List[str]:
    return [
        _normalized(path)
        for path in sorted(glob.glob(os.path.join(root, "hwmon*", "pwm[0-9]*")))
        if _safe_pwm_path(path, root)
    ]


def read_current_pwm(paths: List[str]) -> Optional[int]:
    for path in paths:
        try:
            return int(Path(path).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
    return None


def write_pwm_value(raw_pwm: int, paths: List[str]) -> Optional[str]:
    current = set(discover_pwm_files())
    target = next((path for path in paths if _normalized(path) in current), None)
    if target is None:
        target = next(iter(current), None)
    if target is None or not _safe_pwm_path(target):
        return None
    enable = Path(f"{target}_enable")
    if enable.is_file():
        enable.write_text("1", encoding="utf-8")
    Path(target).write_text(str(raw_pwm), encoding="utf-8")
    return target


def read_hwmon_sensors(root: str = HWMON_ROOT) -> Dict[str, object]:
    temperatures = empty_temperatures()
    fan_rpms: Dict[str, int] = {}
    drive_values = {}
    for directory in sorted(glob.glob(os.path.join(root, "hwmon*"))):
        try:
            chip = Path(directory, "name").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        for input_path in sorted(Path(directory).glob("temp*_input")):
            try:
                raw = float(input_path.read_text(encoding="utf-8").strip())
                value = raw / 1000 if raw > 500 else raw
            except (OSError, ValueError):
                continue
            label_path = input_path.with_name(input_path.name.replace("_input", "_label"))
            try:
                label = label_path.read_text(encoding="utf-8").strip().lower()
            except OSError:
                label = input_path.stem.lower()
            identity = f"{chip} {label}"
            if any(marker in identity for marker in ("coretemp", "k10temp", "package", "cpu")):
                temperatures["cpu_c"] = max(temperatures["cpu_c"] or value, value)
            elif any(marker in identity for marker in ("drivetemp", "hdd", "ata")):
                drive_values[f"{chip}:{label}"] = value
            elif any(marker in identity for marker in ("board", "pch", "chipset", "motherboard")):
                temperatures["board_c"] = max(temperatures["board_c"] or value, value)
        for fan_path in sorted(Path(directory).glob("fan*_input")):
            try:
                fan_rpms[f"{chip}:{fan_path.stem.removesuffix('_input')}"] = int(
                    fan_path.read_text(encoding="utf-8").strip()
                )
            except (OSError, ValueError):
                continue
    temperatures["drives_c"] = drive_values
    temperatures["max_drive_c"] = max(drive_values.values()) if drive_values else None
    return {
        "temperatures": temperatures,
        "fan_rpms": fan_rpms,
        "sensor_ok": temperatures["cpu_c"] is not None and temperatures["max_drive_c"] is not None,
    }


class HwmonPwmBackend:
    def __init__(
        self,
        *,
        discover: Callable[[], List[str]] = discover_pwm_files,
        read: Callable[[List[str]], Optional[int]] = read_current_pwm,
        write: Callable[[int, List[str]], Optional[str]] = write_pwm_value,
        sensor_reader: Callable[[], Dict[str, object]] = read_hwmon_sensors,
    ) -> None:
        self._discover = discover
        self._read = read
        self._write = write
        self._sensor_reader = sensor_reader

    def status(self) -> BackendStatus:
        paths = self._discover()
        raw = self._read(paths)
        sensors = self._sensor_reader()
        return BackendStatus(
            backend="hwmon_pwm",
            mode="manual" if raw is not None else "unknown",
            duty_percent=pwm_to_percent(raw) if raw is not None else None,
            pwm=raw,
            fan_rpms=sensors.get("fan_rpms") or {},
            temperatures=sensors.get("temperatures") or empty_temperatures(),
            sensor_ok=bool(sensors.get("sensor_ok")),
            error=None if sensors.get("sensor_ok") else {
                "code": "sensor_read_failed",
                "message": "Required hwmon sensors are unavailable",
            },
        )

    def set_duty_percent(self, percent: int) -> BackendStatus:
        expected = clamp_percent(percent)
        paths = self._discover()
        if self._write(percent_to_pwm(expected), paths) is None:
            raise BackendMutationError("pwm_write_failed")
        status = self.status()
        if status.duty_percent != expected:
            raise BackendMutationError("readback_mismatch")
        return status

    def set_auto(self) -> BackendStatus:
        paths = self._discover()
        changed = False
        for path in paths:
            enable = Path(f"{path}_enable")
            if enable.is_file():
                enable.write_text("2", encoding="utf-8")
                changed = True
        if not changed:
            raise BackendMutationError("auto_mode_unavailable")
        status = self.status()
        status.mode = "auto"
        return status
