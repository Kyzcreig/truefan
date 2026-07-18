from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readme_is_an_operator_guide_for_both_backends_and_fail_safe_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for text in (
        "TRUEFAN_COMPONENT",
        "ast2600_ipmi",
        "hwmon_pwm",
        "CONTROL_AGENT_TOKEN_FILE",
        "TRUEFAN_UI_WRITE_TOKEN_FILE",
        "BMC_USER_FILE",
        "BMC_PASSWORD_FILE",
        "TRUENAS_USER_FILE",
        "TRUENAS_PASSWORD_FILE",
        "22%",
        "44°C",
        "70°C",
        "900",
        "External watchdog",
        "30082",
        "docker compose",
        "Rollback",
    ):
        assert text in readme

    assert "-P" not in readme
    assert "privileged: true" not in readme
