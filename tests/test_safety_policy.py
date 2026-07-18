import json
import threading
import time
from typing import cast

import pytest

from truefan_control.ast2600 import percent_to_pwm
from truefan_control.backend import BackendStatus
from truefan_control.policy import ControlStateStore, SafetyLocked, SafetyPolicy
from truefan_control.service import ControlService


def backend_status(*, cpu=45, hdd=35, duty=22, sensor_ok=True):
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


def make_policy(tmp_path, now=100.0):
    def clock():
        return now

    store = ControlStateStore(tmp_path / "control-state.json", clock=clock)
    return SafetyPolicy(store, clock=clock)


def test_hot_cooling_and_recovered_policy_transitions(tmp_path):
    policy = make_policy(tmp_path)

    hot = policy.evaluate(backend_status(cpu=71, hdd=42, duty=22))
    cooling = policy.evaluate(backend_status(cpu=65, hdd=43, duty=100))
    recovered = policy.evaluate(backend_status(cpu=60, hdd=40, duty=50))

    assert (hot.state, hot.effective_duty, hot.reason) == ("hot", 100, "hot_threshold")
    assert (cooling.state, cooling.effective_duty) == ("cooling", 50)
    assert (recovered.state, recovered.effective_duty) == ("normal", 22)


def test_sensor_failure_never_lowers_and_unknown_duty_forces_100(tmp_path):
    policy = make_policy(tmp_path)

    known = policy.evaluate(backend_status(sensor_ok=False, duty=70))
    unknown = policy.evaluate(backend_status(sensor_ok=False, duty=None))

    assert (known.state, known.effective_duty) == ("sensor-failure", 70)
    assert (unknown.state, unknown.effective_duty) == ("sensor-failure", 100)


def test_manual_ttl_bounds_atomic_round_trip_and_expired_restart(tmp_path):
    state_path = tmp_path / "control-state.json"
    now = [100.0]

    def clock():
        return now[0]

    policy = SafetyPolicy(ControlStateStore(state_path, clock=clock), clock=clock)

    with pytest.raises(ValueError, match="ttl_out_of_range"):
        policy.request_override(backend_status(), 50, 0)
    with pytest.raises(ValueError, match="ttl_out_of_range"):
        policy.request_override(backend_status(), 50, 901)

    decision = policy.request_override(backend_status(), 50, 300)
    assert decision.effective_duty == 50
    assert json.loads(state_path.read_text(encoding="utf-8"))["override"] == {
        "duty_percent": 50,
        "expires_at": 400.0,
    }
    assert not list(tmp_path.glob(".control-state.*"))

    now[0] = 200.0
    restarted = SafetyPolicy(ControlStateStore(state_path, clock=clock), clock=clock)
    assert restarted.evaluate(backend_status()).effective_duty == 50

    now[0] = 401.0
    expired_restart = SafetyPolicy(ControlStateStore(state_path, clock=clock), clock=clock)
    expired = expired_restart.evaluate(backend_status())
    assert expired.effective_duty == 22
    assert json.loads(state_path.read_text(encoding="utf-8"))["override"] is None


class FakeBackend:
    def __init__(self, status):
        self.current = status
        self.writes = []

    def status(self):
        return self.current

    def set_duty_percent(self, percent):
        self.writes.append(percent)
        self.current.duty_percent = percent
        self.current.pwm = percent_to_pwm(percent)
        self.current.mode = "manual"
        return self.current

    def set_auto(self):
        raise AssertionError("not used")


def test_low_manual_request_while_hot_is_409_ready_and_does_not_write(tmp_path):
    backend = FakeBackend(backend_status(cpu=75, hdd=48, duty=100))
    service = ControlService(backend, make_policy(tmp_path))

    with pytest.raises(SafetyLocked) as raised:
        service.request_duty(50, 300)

    assert raised.value.code == "safety_locked"
    assert backend.writes == []


def test_sensor_failure_manual_request_cannot_lower_known_duty(tmp_path):
    backend = FakeBackend(backend_status(sensor_ok=False, duty=70))
    service = ControlService(backend, make_policy(tmp_path))

    with pytest.raises(SafetyLocked):
        service.request_duty(50, 300)

    assert backend.writes == []


def test_invalid_persisted_override_is_discarded(tmp_path):
    state_path = tmp_path / "control-state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "previous_hot": False,
                "override": {"duty_percent": 200, "expires_at": 9999.0},
            }
        ),
        encoding="utf-8",
    )

    policy = SafetyPolicy(ControlStateStore(state_path, clock=lambda: 100.0), clock=lambda: 100.0)

    assert policy.evaluate(backend_status()).effective_duty == 22


def test_service_serializes_policy_and_backend_mutations(tmp_path):
    class ConcurrentBackend(FakeBackend):
        def __init__(self, status):
            super().__init__(status)
            self.active = 0
            self.max_active = 0
            self.guard = threading.Lock()

        def set_duty_percent(self, percent):
            with self.guard:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.03)
            try:
                return super().set_duty_percent(percent)
            finally:
                with self.guard:
                    self.active -= 1

    backend = ConcurrentBackend(backend_status())
    service = ControlService(backend, make_policy(tmp_path))
    barrier = threading.Barrier(3)

    def request(duty):
        barrier.wait()
        service.request_duty(duty, 300)

    threads = [threading.Thread(target=request, args=(duty,)) for duty in (60, 70)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert backend.max_active == 1


def test_status_returns_cached_snapshot_while_tick_is_refreshing(tmp_path):
    class BlockingBackend(FakeBackend):
        def __init__(self, status):
            super().__init__(status)
            self.block_reads = False
            self.read_started = threading.Event()
            self.release_read = threading.Event()

        def status(self):
            if self.block_reads:
                self.read_started.set()
                self.release_read.wait(timeout=2)
            return super().status()

    backend = BlockingBackend(backend_status(duty=22))
    service = ControlService(backend, make_policy(tmp_path))
    service.tick()
    backend.block_reads = True

    refresh = threading.Thread(target=service.tick)
    refresh.start()
    assert backend.read_started.wait(timeout=1)

    snapshots = []
    reader = threading.Thread(target=lambda: snapshots.append(service.status()))
    reader.start()
    reader.join(timeout=0.1)
    returned_without_waiting = not reader.is_alive()

    backend.release_read.set()
    refresh.join(timeout=2)
    reader.join(timeout=2)

    assert returned_without_waiting, "status blocked behind a hardware refresh"
    assert snapshots[0]["backend"]["duty_percent"] == 22


def test_successful_control_mutation_replaces_cached_snapshot(tmp_path):
    backend = FakeBackend(backend_status(duty=22))
    service = ControlService(backend, make_policy(tmp_path))
    service.tick()

    result = service.request_duty(50, 300)
    snapshot = service.status()
    readback = cast(dict, result["readback"])
    backend_snapshot = cast(dict, snapshot["backend"])
    safety_snapshot = cast(dict, snapshot["safety"])

    assert readback["duty_percent"] == 50
    assert backend_snapshot["duty_percent"] == 50
    assert safety_snapshot["effective_duty"] == 50


def test_failed_refresh_invalidates_cached_green_until_next_success(tmp_path):
    class FailingBackend(FakeBackend):
        fail_reads = False

        def status(self):
            if self.fail_reads:
                raise RuntimeError("simulated hardware read failure")
            return super().status()

    backend = FailingBackend(backend_status(duty=22))
    service = ControlService(backend, make_policy(tmp_path))
    service.tick()
    warm_backend = cast(dict, service.status()["backend"])
    assert warm_backend["sensor_ok"] is True

    backend.fail_reads = True
    with pytest.raises(RuntimeError, match="simulated hardware read failure"):
        service.tick()

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="status_refresh_failed"):
        service.status()
    assert time.monotonic() - started < 0.05

    backend.fail_reads = False
    service.tick()
    recovered_backend = cast(dict, service.status()["backend"])
    assert recovered_backend["sensor_ok"] is True
