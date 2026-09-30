from truefan_control.ast2600 import percent_to_pwm
from truefan_control.backend import BackendStatus
from truefan_control.policy import (
    DRIVE_SPEC_MAX_C,
    FULL_FAN_STEADY_DRIVE_C,
    ControlStateStore,
    SafetyPolicy,
    HOT_CPU_C,
    HOT_DRIVE_C,
    RECOVER_CPU_C,
    RECOVER_DRIVE_C,
    THRESHOLDS,
)
from truefan_control.service import ControlService


def _status(*, cpu=45, hdd=35, duty=22, sensor_ok=True):
    return BackendStatus(
        backend="fake",
        mode="manual" if duty is not None else "unknown",
        duty_percent=duty,
        pwm=percent_to_pwm(duty) if duty is not None else None,
        temperatures={
            "cpu_c": cpu,
            "board_c": 36,
            "drives_c": {"sda": hdd} if hdd is not None else {},
            "max_drive_c": hdd,
            "nvme_c": 40,
        },
        sensor_ok=sensor_ok,
    )


def _policy(tmp_path, now=100.0):
    store = ControlStateStore(tmp_path / "control-state.json", clock=lambda: now)
    return SafetyPolicy(store, clock=lambda: now)


class _FakeBackend:
    def status(self):
        return _status()

    def set_duty_percent(self, duty):
        return _status(duty=duty)


def test_service_publishes_thresholds(tmp_path):
    service = ControlService(_FakeBackend(), _policy(tmp_path))
    published = service.tick()
    assert "thresholds" in published
    assert published["thresholds"]["max_drive_c"]["hot"] == HOT_DRIVE_C
    assert published["thresholds"]["cpu_c"]["hot"] == HOT_CPU_C


def test_display_thresholds_match_safety_constants():
    # Anti-drift: the numbers the dashboard colours from equal the numbers the
    # safety policy actually trips on. If someone tunes one, this fails.
    assert THRESHOLDS["max_drive_c"]["hot"] == HOT_DRIVE_C
    assert THRESHOLDS["cpu_c"]["hot"] == HOT_CPU_C
    assert THRESHOLDS["recover"] == {"drive": RECOVER_DRIVE_C, "cpu": RECOVER_CPU_C}


def test_policy_trips_hot_exactly_at_constants(tmp_path):
    policy = _policy(tmp_path)
    assert policy.evaluate(_status(cpu=HOT_CPU_C + 1, hdd=20)).state == "hot"
    assert policy.evaluate(_status(cpu=20, hdd=HOT_DRIVE_C + 1)).state == "hot"
    assert policy.evaluate(_status(cpu=HOT_CPU_C, hdd=HOT_DRIVE_C)).state != "hot"


def test_drive_thresholds_are_ordered_under_the_datasheet_limit():
    # recover < warm <= hot, and hot stays 10 C under the lowest drive datasheet max.
    warm = THRESHOLDS["max_drive_c"]["warm"]
    assert RECOVER_DRIVE_C < warm <= HOT_DRIVE_C
    assert HOT_DRIVE_C <= DRIVE_SPEC_MAX_C - 10


def test_recovery_is_reachable_at_full_fan_steady_state(tmp_path):
    # Regression for 2026-09-29: recover was 40 C while 100% fans plateaued the drives
    # at 46 C, so a hot incident pinned fans at 100% until ambient dropped.
    assert RECOVER_DRIVE_C >= FULL_FAN_STEADY_DRIVE_C
    policy = _policy(tmp_path)
    assert policy.evaluate(_status(cpu=46, hdd=HOT_DRIVE_C + 1, duty=22)).state == "hot"
    cooling = policy.evaluate(_status(cpu=46, hdd=RECOVER_DRIVE_C + 1, duty=100))
    assert (cooling.state, cooling.effective_duty) == ("cooling", 50)
    recovered = policy.evaluate(_status(cpu=46, hdd=FULL_FAN_STEADY_DRIVE_C, duty=50))
    assert (recovered.state, recovered.effective_duty, recovered.reason) == ("normal", 22, "recovered")
