"""A hot CPU reading forces 100% in ONE policy tick, whatever else failed (t_59d5e3a8).

2026-10-17 13:48-14:02: Tctl held 90-92 C under a 12-thread stress on the Fleet NAS. The
documented rule is "CPU > 70 C -> 100%". The sensor-failure branch held the current duty
(22%) whenever any source failed, even with a valid hot CPU reading in hand, which is the
same blind spot nas-fan-watch had (t_dc2d8acf). These drive a fake 90 C reading through
ControlService.tick() and assert the backend write.
"""
import logging

import pytest

from truefan_control.ast2600 import Ast2600Backend, percent_to_pwm
from truefan_control.backend import BackendStatus
from truefan_control.policy import ControlStateStore, SafetyPolicy
from truefan_control.service import ControlService


class FakeIpmi:
    def __init__(self, duty=22):
        self.duty = duty
        self.writes = []

    def status(self):
        return BackendStatus(backend="ast2600_ipmi", mode="manual", duty_percent=self.duty,
                             pwm=percent_to_pwm(self.duty), sensor_ok=True)

    def set_duty_percent(self, percent):
        self.writes.append(percent)
        self.duty = percent
        return self.status()


class FakeRedfish:
    def __init__(self, cpu):
        self.cpu = cpu

    def fetch(self):
        return {"cpu_c": self.cpu, "board_c": None, "fan_rpms": {"FAN1_1": 400}}


class FakeTrueNas:
    def __init__(self, drive=None, fail=False):
        self.drive = drive
        self.fail = fail

    def fetch(self):
        if self.fail:
            raise RuntimeError("truenas_connect_failed")
        return {"drives_c": {"sda": self.drive}, "max_drive_c": self.drive, "nvme_c": 40.0}


def make_service(tmp_path, *, cpu, drive=40.0, drive_fail=False):
    ipmi = FakeIpmi(duty=22)
    backend = Ast2600Backend(ipmi, FakeRedfish(cpu), FakeTrueNas(drive, fail=drive_fail))
    policy = SafetyPolicy(ControlStateStore(tmp_path / "control-state.json"))
    return ControlService(backend, policy), ipmi


def test_hot_cpu_with_all_sensors_ok_writes_100_in_one_tick(tmp_path):
    service, ipmi = make_service(tmp_path, cpu=90.0)
    status = service.tick()
    assert ipmi.writes == [100]
    assert status["safety"]["state"] == "hot"
    assert status["backend"]["duty_percent"] == 100


@pytest.mark.parametrize("drive_fail,drive", [(True, None), (False, None)])
def test_hot_cpu_while_drive_read_fails_still_writes_100(tmp_path, drive_fail, drive):
    # drive_fail: the TrueNAS WS read raised; drive=None: it answered without HDD values.
    service, ipmi = make_service(tmp_path, cpu=90.0, drive=drive, drive_fail=drive_fail)
    status = service.tick()
    assert status["backend"]["sensor_ok"] is False
    assert ipmi.writes == [100], "a valid 90 C CPU reading must force 100% even when the drive read failed"
    assert status["safety"]["state"] == "hot"


def test_hot_drive_while_cpu_read_missing_still_writes_100(tmp_path):
    service, ipmi = make_service(tmp_path, cpu=None, drive=55.0)
    service.tick()
    assert ipmi.writes == [100]


def test_cool_cpu_with_failed_drive_read_still_fails_closed_unchanged(tmp_path):
    # The fail-closed hold is unchanged when nothing reads hot: no write, duty stays 22.
    service, ipmi = make_service(tmp_path, cpu=45.0, drive_fail=True)
    status = service.tick()
    assert ipmi.writes == []
    assert status["safety"]["state"] == "sensor-failure"


def test_hot_cpu_latches_previous_hot_so_cooling_band_follows(tmp_path):
    service, ipmi = make_service(tmp_path, cpu=90.0, drive_fail=True)
    service.tick()
    service.backend._redfish.cpu = 65.0
    service.backend._truenas.fail = False
    service.backend._truenas.drive = 40.0
    status = service.tick()
    assert status["safety"]["state"] == "cooling"
    assert ipmi.writes == [100, 50]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "hot"])
def test_non_numeric_cpu_reading_is_not_hot(tmp_path, bad):
    service, ipmi = make_service(tmp_path, cpu=bad, drive_fail=True)
    service.tick()
    assert ipmi.writes == []


def test_tick_logs_one_line_per_transition(tmp_path, caplog):
    service, _ = make_service(tmp_path, cpu=90.0, drive_fail=True)
    with caplog.at_level(logging.WARNING, logger="truefan-control"):
        service.tick()
        service.tick()  # same state: no second line
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("policy ")]
    assert len(lines) == 1
    assert "state=hot duty=100" in lines[0] and "cpu_c=90.0" in lines[0] and "sensor_ok=False" in lines[0]
