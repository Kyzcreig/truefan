import pytest

from truefan_control.ast2600 import Ast2600Backend
from truefan_control.backend import BackendStatus
from truefan_control.config import ConfigError, Settings, read_secret_file
from truefan_control.hwmon_backend import HwmonPwmBackend


class FakeIpmi:
    def status(self):
        return BackendStatus(backend="ast2600_ipmi", mode="manual", duty_percent=50, pwm=128)

    def set_duty_percent(self, percent):
        return BackendStatus(backend="ast2600_ipmi", mode="manual", duty_percent=percent, pwm=128)

    def set_auto(self):
        return BackendStatus(backend="ast2600_ipmi", mode="auto", duty_percent=50, pwm=128)


class FakeRedfish:
    def fetch(self):
        return {"cpu_c": 61.0, "board_c": 42.0, "fan_rpms": {"FAN1": 2050}}


class FakeTrueNas:
    def fetch(self):
        return {
            "drives_c": {"sda": 40.0, "nvme0": 52.0},
            "max_drive_c": 40.0,
            "nvme_c": 52.0,
        }


def test_ast2600_backend_combines_ipmi_redfish_and_truenas():
    backend = Ast2600Backend(FakeIpmi(), FakeRedfish(), FakeTrueNas())

    status = backend.status()

    assert status.sensor_ok is True
    assert status.fan_rpms == {"FAN1": 2050}
    assert status.temperatures == {
        "cpu_c": 61.0,
        "board_c": 42.0,
        "drives_c": {"sda": 40.0, "nvme0": 52.0},
        "max_drive_c": 40.0,
        "nvme_c": 52.0,
    }
    assert status.error is None


def test_ast2600_backend_sensor_exception_is_fail_closed_and_sanitized():
    class BrokenTrueNas:
        def fetch(self):
            raise RuntimeError("password=do-not-leak")

    backend = Ast2600Backend(FakeIpmi(), FakeRedfish(), BrokenTrueNas())

    status = backend.status()

    assert status.sensor_ok is False
    assert status.error == {"code": "sensor_read_failed", "message": "One or more sensor sources failed"}
    assert "do-not-leak" not in str(status.to_dict())


def test_hwmon_backend_preserves_pwm_adapter_behavior_with_percent_contract():
    writes = []
    current = [128]

    def write(raw_pwm, paths):
        writes.append((raw_pwm, paths))
        current[0] = raw_pwm
        return paths[0]

    backend = HwmonPwmBackend(
        discover=lambda: ["/safe/pwm1"],
        read=lambda _paths: current[0],
        write=write,
        sensor_reader=lambda: {
            "temperatures": {
                "cpu_c": 45.0,
                "board_c": 38.0,
                "drives_c": {"sda": 39.0},
                "max_drive_c": 39.0,
                "nvme_c": None,
            },
            "fan_rpms": {"fan1": 1200},
            "sensor_ok": True,
        },
    )

    before = backend.status()
    after = backend.set_duty_percent(50)

    assert before.backend == "hwmon_pwm"
    assert before.duty_percent == 50
    assert writes == [(128, ["/safe/pwm1"])]
    assert after.duty_percent == 50
    assert after.sensor_ok is True


def test_settings_load_secrets_only_from_files_and_hide_values_from_repr(tmp_path):
    secret = tmp_path / "agent-token"
    secret.write_text("agent-super-secret\n", encoding="utf-8")
    environ = {
        "TRUEFAN_BACKEND": "hwmon_pwm",
        "TRUEFAN_AGENT_SECRET_FILE": str(secret),
        "CONTROL_STATE_PATH": str(tmp_path / "state.json"),
    }

    assert read_secret_file("TRUEFAN_AGENT_SECRET_FILE", environ=environ) == "agent-super-secret"
    settings = Settings.from_env(environ=environ)

    assert settings.backend == "hwmon_pwm"
    assert settings.agent_token == "agent-super-secret"
    assert "agent-super-secret" not in repr(settings)

    with pytest.raises(ConfigError, match="TRUEFAN_AGENT_SECRET_FILE"):
        Settings.from_env(environ={"TRUEFAN_BACKEND": "hwmon_pwm"})
