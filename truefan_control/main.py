import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .ast2600 import pwm_to_percent
from .config import Settings
from .factory import build_service
from .policy import SafetyLocked


LOGGER = logging.getLogger("truefan-control")


class ControlBody(BaseModel):
    duty_percent: int = Field(..., ge=22, le=100)
    ttl_seconds: int = Field(default=300, ge=1, le=900)


class PwmBody(BaseModel):
    pwm: int = Field(..., ge=0, le=255)
    ttl_seconds: int = Field(default=300, ge=1, le=900)


class TtlBody(BaseModel):
    ttl_seconds: int = Field(default=300, ge=1, le=900)


def _result(ok: bool, data=None, code: Optional[str] = None, message: Optional[str] = None):
    return {"ok": ok, "error": None if ok else {"code": code, "message": message}, "data": data if ok else None}


def create_app(*, service=None, expected_token: Optional[str] = None) -> FastAPI:
    supplied_service = service

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        task = None
        if supplied_service is None:
            settings = Settings.from_env()
            application.state.service = build_service(settings)
            application.state.expected_token = settings.agent_token

            async def policy_loop():
                while True:
                    try:
                        await asyncio.to_thread(application.state.service.tick)
                    except Exception:
                        LOGGER.error("Policy loop iteration failed")
                    await asyncio.sleep(settings.policy_interval_seconds)

            task = asyncio.create_task(policy_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    application = FastAPI(title="truefan-control", lifespan=lifespan)
    application.state.service = service
    application.state.expected_token = expected_token

    def require_token(request: Request, authorization: str = Header(default="")) -> None:
        expected = request.app.state.expected_token or ""
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        presented = authorization[len("Bearer ") :].strip()
        if not expected or not secrets.compare_digest(presented, expected):
            raise HTTPException(status_code=403, detail="Invalid token")

    def current_service(request: Request):
        if request.app.state.service is None:
            raise HTTPException(status_code=503, detail="Agent is not initialized")
        return request.app.state.service

    @application.get("/status")
    def status(_: None = Depends(require_token), active=Depends(current_service)):
        return _result(True, active.status())

    @application.post("/control")
    def control(body: ControlBody, _: None = Depends(require_token), active=Depends(current_service)):
        return _result(True, active.request_duty(body.duty_percent, body.ttl_seconds))

    @application.post("/set_pwm")
    def set_pwm(body: PwmBody, _: None = Depends(require_token), active=Depends(current_service)):
        data = active.request_duty(pwm_to_percent(body.pwm), body.ttl_seconds)
        data["pwm"] = (data.get("readback") or {}).get("pwm", body.pwm)
        return _result(True, data)

    @application.post("/profile/{profile}")
    def profile(profile: str, body: TtlBody, _: None = Depends(require_token), active=Depends(current_service)):
        return _result(True, active.request_profile(profile, body.ttl_seconds))

    @application.exception_handler(SafetyLocked)
    async def safety_locked(_request, exc: SafetyLocked):
        return JSONResponse(status_code=409, content=_result(False, code=exc.code, message=str(exc)))

    @application.exception_handler(ValueError)
    async def invalid_request(_request, exc: ValueError):
        code = str(exc) if str(exc) in {
            "duty_out_of_range",
            "ttl_out_of_range",
            "unknown_profile",
        } else "invalid_request"
        return JSONResponse(status_code=400, content=_result(False, code=code, message="Invalid control request"))

    @application.exception_handler(Exception)
    async def unexpected(_request, _exc: Exception):
        LOGGER.error("Unhandled control-agent error")
        return JSONResponse(
            status_code=500,
            content=_result(False, code="internal_error", message="Internal control-agent error"),
        )

    return application


app = create_app()
