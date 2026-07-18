from dataclasses import asdict, dataclass, field
from typing import Dict, Mapping, Optional, Protocol


def empty_temperatures() -> Dict[str, object]:
    return {
        "cpu_c": None,
        "board_c": None,
        "drives_c": {},
        "max_drive_c": None,
        "nvme_c": None,
    }


@dataclass
class BackendStatus:
    backend: str
    mode: str = "unknown"
    duty_percent: Optional[int] = None
    pwm: Optional[int] = None
    fan_rpms: Mapping[str, int] = field(default_factory=dict)
    temperatures: Mapping[str, object] = field(default_factory=empty_temperatures)
    sensor_ok: bool = False
    error: Optional[Mapping[str, str]] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class FanBackend(Protocol):
    def status(self) -> BackendStatus: ...

    def set_duty_percent(self, percent: int) -> BackendStatus: ...

    def set_auto(self) -> BackendStatus: ...
