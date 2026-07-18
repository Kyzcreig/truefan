from types import SimpleNamespace

import pytest

from truefan_control.ast2600 import (
    Ast2600Ipmi,
    BackendMutationError,
    clamp_percent,
    percent_to_pwm,
    pwm_to_percent,
)


BASE = ["ipmitool", "-I", "lanplus", "-H", "bmc.test", "-U", "operator", "-E"]


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        stdout = next(self.outputs)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_pwm_percent_mapping_and_safe_clamp():
    assert percent_to_pwm(22) == 56
    assert percent_to_pwm(50) == 128
    assert percent_to_pwm(100) == 255
    assert pwm_to_percent(0) == 22
    assert pwm_to_percent(128) == 50
    assert pwm_to_percent(255) == 100
    assert clamp_percent(-10) == 22
    assert clamp_percent(101) == 100


def test_exact_read_commands_and_password_is_environment_only():
    runner = FakeRunner(["02 " * 16, "32 " * 16])
    ipmi = Ast2600Ipmi("bmc.test", "operator", "do-not-leak", runner=runner)

    assert ipmi.read_mode() == "manual"
    assert ipmi.read_duty_percent() == 50

    assert runner.calls[0][0] == BASE + ["raw", "0x3a", "0xd0", "0x12"]
    assert runner.calls[1][0] == BASE + ["raw", "0x3a", "0xd0", "0x0f"]
    for argv, kwargs in runner.calls:
        assert kwargs["shell"] is False
        assert kwargs["env"]["IPMI_PASSWORD"] == "do-not-leak"
        assert "do-not-leak" not in argv
        assert "-P" not in argv


def test_exact_manual_and_duty_commands_are_verified_by_readback():
    runner = FakeRunner(["", "", "02 " * 16, "2d " * 16])
    ipmi = Ast2600Ipmi("bmc.test", "operator", "secret", runner=runner)

    status = ipmi.set_duty_percent(45)

    assert runner.calls[0][0] == BASE + ["raw", "0x3a", "0xd0", "0x11"] + ["0x2"] * 16
    assert runner.calls[1][0] == BASE + ["raw", "0x3a", "0xd0", "0x0e"] + ["0x2d"] * 16
    assert runner.calls[2][0] == BASE + ["raw", "0x3a", "0xd0", "0x12"]
    assert runner.calls[3][0] == BASE + ["raw", "0x3a", "0xd0", "0x0f"]
    assert status.mode == "manual"
    assert status.duty_percent == 45
    assert status.pwm == 115


def test_exact_auto_command_reads_back_mode_and_duty():
    runner = FakeRunner(["", "00 " * 16, "32 " * 16])
    ipmi = Ast2600Ipmi("bmc.test", "operator", "secret", runner=runner)

    status = ipmi.set_auto()

    assert runner.calls[0][0] == BASE + ["raw", "0x3a", "0xd0", "0x11"] + ["0x0"] * 16
    assert runner.calls[1][0] == BASE + ["raw", "0x3a", "0xd0", "0x12"]
    assert runner.calls[2][0] == BASE + ["raw", "0x3a", "0xd0", "0x0f"]
    assert status.mode == "auto"
    assert status.duty_percent == 50


def test_readback_mismatch_fails_mutation():
    runner = FakeRunner(["", "", "02 " * 16, "31 " * 16])
    ipmi = Ast2600Ipmi("bmc.test", "operator", "secret", runner=runner)

    with pytest.raises(BackendMutationError, match="readback_mismatch"):
        ipmi.set_duty_percent(50)
