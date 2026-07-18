import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


class ConfigError(RuntimeError):
    """A startup configuration error that never includes secret content."""


def read_secret_file(name: str, *, environ: Optional[Mapping[str, str]] = None) -> str:
    source = os.environ if environ is None else environ
    path_value = source.get(name, "").strip()
    if not path_value:
        raise ConfigError(f"{name} is required")
    try:
        value = Path(path_value).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"{name} could not be read") from exc
    if not value:
        raise ConfigError(f"{name} is empty")
    return value


def _required(name: str, environ: Mapping[str, str]) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} is required")
    return value


def _bool(name: str, environ: Mapping[str, str], default: bool = True) -> bool:
    raw = environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true or false")


@dataclass
class Settings:
    backend: str
    agent_token: str = field(repr=False)
    state_path: str = "/data/control-state.json"
    policy_interval_seconds: float = 5.0
    bmc_host: Optional[str] = None
    bmc_user: Optional[str] = field(default=None, repr=False)
    bmc_password: Optional[str] = field(default=None, repr=False)
    bmc_verify_tls: bool = True
    truenas_host: Optional[str] = None
    truenas_user: Optional[str] = field(default=None, repr=False)
    truenas_password: Optional[str] = field(default=None, repr=False)
    truenas_verify_tls: bool = True

    @classmethod
    def from_env(cls, *, environ: Optional[Mapping[str, str]] = None):
        source = os.environ if environ is None else environ
        backend = source.get("TRUEFAN_BACKEND", "hwmon_pwm").strip().lower()
        if backend not in {"hwmon_pwm", "ast2600_ipmi"}:
            raise ConfigError("TRUEFAN_BACKEND must be hwmon_pwm or ast2600_ipmi")
        try:
            interval = float(source.get("TRUEFAN_POLICY_INTERVAL_SECONDS", "5"))
        except ValueError as exc:
            raise ConfigError("TRUEFAN_POLICY_INTERVAL_SECONDS must be numeric") from exc
        if interval <= 0 or interval > 60:
            raise ConfigError("TRUEFAN_POLICY_INTERVAL_SECONDS is out of range")
        values = {
            "backend": backend,
            "agent_token": read_secret_file("TRUEFAN_AGENT_SECRET_FILE", environ=source),
            "state_path": source.get("CONTROL_STATE_PATH", "/data/control-state.json"),
            "policy_interval_seconds": interval,
        }
        if backend == "ast2600_ipmi":
            values.update(
                {
                    "bmc_host": _required("BMC_HOST", source),
                    "bmc_user": read_secret_file("BMC_USER_FILE", environ=source),
                    "bmc_password": read_secret_file("BMC_PASSWORD_FILE", environ=source),
                    "bmc_verify_tls": _bool("BMC_TLS_VERIFY", source, True),
                    "truenas_host": _required("TRUENAS_HOST", source),
                    "truenas_user": read_secret_file("TRUENAS_USER_FILE", environ=source),
                    "truenas_password": read_secret_file("TRUENAS_PASSWORD_FILE", environ=source),
                    "truenas_verify_tls": _bool("TRUENAS_TLS_VERIFY", source, True),
                }
            )
        return cls(**values)
