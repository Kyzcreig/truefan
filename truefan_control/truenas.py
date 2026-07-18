import json
import ssl
from typing import Callable, Dict, Optional


class SensorSourceError(RuntimeError):
    """A sanitized TrueNAS sensor failure."""


class TrueNasTemperatureClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        timeout_seconds: float = 4.0,
        connector: Optional[Callable] = None,
    ) -> None:
        self._url = f"wss://{host}/api/current"
        self._username = username
        self._password = password
        self._verify_tls = verify_tls
        self._timeout_seconds = timeout_seconds
        self._connector = connector

    def _connect(self):
        connector = self._connector
        if connector is None:
            import websocket

            connector = websocket.create_connection
        return connector(
            self._url,
            timeout=self._timeout_seconds,
            sslopt={"cert_reqs": ssl.CERT_REQUIRED if self._verify_tls else ssl.CERT_NONE},
        )

    @staticmethod
    def _rpc(socket, request_id: int, method: str, params):
        socket.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
        )
        try:
            response = json.loads(socket.recv())
        except Exception as exc:
            raise SensorSourceError("truenas_invalid_response") from exc
        if not isinstance(response, dict) or response.get("id") != request_id:
            raise SensorSourceError("truenas_invalid_response")
        if response.get("error") is not None:
            raise SensorSourceError("truenas_rpc_failed")
        if "result" not in response:
            raise SensorSourceError("truenas_invalid_response")
        return response["result"]

    def fetch(self) -> Dict[str, object]:
        try:
            socket = self._connect()
        except Exception as exc:
            raise SensorSourceError("truenas_connect_failed") from exc
        try:
            auth_result = self._rpc(
                socket,
                1,
                "auth.login_ex",
                [
                    {
                        "mechanism": "PASSWORD_PLAIN",
                        "username": self._username,
                        "password": self._password,
                    }
                ],
            )
            auth_succeeded = auth_result is True or (
                isinstance(auth_result, dict)
                and auth_result.get("response_type") == "SUCCESS"
            )
            if not auth_succeeded:
                raise SensorSourceError("truenas_auth_failed")
            temperatures = self._rpc(socket, 2, "disk.temperatures", [[]])
        finally:
            socket.close()

        if not isinstance(temperatures, dict):
            raise SensorSourceError("truenas_invalid_temperatures")

        drives: Dict[str, float] = {}
        for name, raw_value in temperatures.items():
            if isinstance(raw_value, bool) or raw_value is None:
                continue
            try:
                drives[str(name)] = float(raw_value)
            except (TypeError, ValueError):
                continue

        hdd_values = [value for name, value in drives.items() if "nvme" not in name.lower()]
        nvme_values = [value for name, value in drives.items() if "nvme" in name.lower()]
        return {
            "drives_c": drives,
            "max_drive_c": max(hdd_values) if hdd_values else None,
            "nvme_c": max(nvme_values) if nvme_values else None,
        }
