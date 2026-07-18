from .ast2600 import Ast2600Backend, Ast2600Ipmi
from .config import Settings
from .hwmon_backend import HwmonPwmBackend
from .policy import ControlStateStore, SafetyPolicy
from .redfish import RedfishThermalClient
from .service import ControlService
from .truenas import TrueNasTemperatureClient


def build_service(settings: Settings) -> ControlService:
    if settings.backend == "ast2600_ipmi":
        ipmi = Ast2600Ipmi(settings.bmc_host, settings.bmc_user, settings.bmc_password)
        redfish = RedfishThermalClient(
            settings.bmc_host,
            settings.bmc_user,
            settings.bmc_password,
            verify_tls=settings.bmc_verify_tls,
        )
        truenas = TrueNasTemperatureClient(
            settings.truenas_host,
            settings.truenas_user,
            settings.truenas_password,
            verify_tls=settings.truenas_verify_tls,
        )
        backend = Ast2600Backend(ipmi, redfish, truenas)
    else:
        backend = HwmonPwmBackend()
    policy = SafetyPolicy(ControlStateStore(settings.state_path))
    return ControlService(backend, policy)
