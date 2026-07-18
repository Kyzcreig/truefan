import json

import pytest

from truefan_control.redfish import parse_redfish_thermal
from truefan_control.truenas import SensorSourceError, TrueNasTemperatureClient


def test_redfish_parses_cpu_board_and_all_fan_rpms():
    payload = {
        "Temperatures": [
            {"Name": "TEMP_CPU", "ReadingCelsius": 68},
            {"Name": "MB_TEMP1", "ReadingCelsius": 37},
            {"Name": "PCH_TEMP", "ReadingCelsius": 43},
            {"Name": "inlet", "ReadingCelsius": 25},
        ],
        "Fans": [
            {"Name": "FAN1", "Reading": 2100, "ReadingUnits": "RPM"},
            {"Name": "FAN2", "Reading": 1875, "ReadingUnits": "RPM"},
            {"Name": "unused", "Reading": None, "ReadingUnits": "RPM"},
        ],
    }

    parsed = parse_redfish_thermal(payload)

    assert parsed == {
        "cpu_c": 68.0,
        "board_c": 43.0,
        "fan_rpms": {"FAN1": 2100, "FAN2": 1875},
    }


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.sent = []
        self.closed = False

    def send(self, raw):
        self.sent.append(json.loads(raw))

    def recv(self):
        return json.dumps(next(self.responses))

    def close(self):
        self.closed = True


def make_client(responses):
    socket = FakeWebSocket(responses)
    connect_calls = []

    def connect(url, **kwargs):
        connect_calls.append((url, kwargs))
        return socket

    client = TrueNasTemperatureClient(
        "nas.test",
        "reader",
        "never-log-this",
        verify_tls=False,
        timeout_seconds=2.5,
        connector=connect,
    )
    return client, socket, connect_calls


def test_truenas_ws_auth_and_drive_max_nvme_separation():
    client, socket, connect_calls = make_client(
        [
            {"jsonrpc": "2.0", "id": 1, "result": True},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"sda": 39, "sdb": 45, "nvme0n1": 54, "bad": None},
            },
        ]
    )

    result = client.fetch()

    assert result == {
        "drives_c": {"sda": 39.0, "sdb": 45.0, "nvme0n1": 54.0},
        "max_drive_c": 45.0,
        "nvme_c": 54.0,
    }
    assert connect_calls == [
        ("wss://nas.test/api/current", {"timeout": 2.5, "sslopt": {"cert_reqs": 0}})
    ]
    assert socket.sent[0]["method"] == "auth.login_ex"
    assert socket.sent[0]["params"] == [
        {"mechanism": "PASSWORD_PLAIN", "username": "reader", "password": "never-log-this"}
    ]
    assert socket.sent[1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "disk.temperatures",
        "params": [[]],
    }
    assert socket.closed is True


def test_truenas_login_ex_accepts_explicit_success_response():
    client, _, _ = make_client(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"response_type": "SUCCESS", "user_info": {}}},
            {"jsonrpc": "2.0", "id": 2, "result": {"sda": 39}},
        ]
    )

    assert client.fetch()["max_drive_c"] == 39.0


def test_truenas_auth_failure_is_not_a_zero_temperature():
    client, socket, _ = make_client([{"jsonrpc": "2.0", "id": 1, "result": False}])

    with pytest.raises(SensorSourceError, match="truenas_auth_failed"):
        client.fetch()

    assert socket.closed is True


def test_truenas_json_rpc_error_is_sanitized():
    client, _, _ = make_client(
        [
            {"jsonrpc": "2.0", "id": 1, "result": True},
            {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "secret detail"}},
        ]
    )

    with pytest.raises(SensorSourceError) as raised:
        client.fetch()

    assert str(raised.value) == "truenas_rpc_failed"
    assert "secret detail" not in str(raised.value)
