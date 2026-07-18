import base64
import json
import ssl
import urllib.request
from typing import Callable, Dict, Optional


class RedfishError(RuntimeError):
    """A sanitized Redfish read failure."""


def _number(value) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_redfish_thermal(payload: Dict[str, object]) -> Dict[str, object]:
    cpu_values = []
    board_values = []
    for item in payload.get("Temperatures", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("MemberId") or "").strip()
        value = _number(item.get("ReadingCelsius"))
        if value is None:
            continue
        normalized = name.lower()
        if normalized == "temp_cpu" or "cpu" in normalized:
            cpu_values.append(value)
        elif any(marker in normalized for marker in ("board", "mb_", "mainboard", "pch", "chipset")):
            board_values.append(value)

    fans: Dict[str, int] = {}
    for item in payload.get("Fans", []) or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("Name") or item.get("MemberId") or "").strip()
        reading = _number(item.get("ReadingRPM", item.get("Reading")))
        if name and reading is not None and reading >= 0:
            fans[name] = round(reading)

    return {
        "cpu_c": max(cpu_values) if cpu_values else None,
        "board_c": max(board_values) if board_values else None,
        "fan_rpms": fans,
    }


class RedfishThermalClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        timeout_seconds: float = 4.0,
        opener: Callable = urllib.request.urlopen,
    ) -> None:
        self._url = f"https://{host}/redfish/v1/Chassis/Self/Thermal"
        encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self._headers = {"Accept": "application/json", "Authorization": f"Basic {encoded}"}
        self._verify_tls = verify_tls
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def fetch(self) -> Dict[str, object]:
        request = urllib.request.Request(self._url, headers=self._headers, method="GET")
        context = ssl.create_default_context() if self._verify_tls else ssl._create_unverified_context()
        try:
            with self._opener(request, timeout=self._timeout_seconds, context=context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RedfishError("redfish_read_failed") from exc
        if not isinstance(payload, dict):
            raise RedfishError("redfish_invalid_response")
        return parse_redfish_thermal(payload)
