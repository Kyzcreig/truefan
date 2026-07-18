# TrueFan AST2600 + TrueNAS Build Contract

Status: approved for Fleet NAS implementation

Target: TrueNAS SCALE 25.10.4, ASRock Rack B650D4U-2L2T/BCM, AST2600 BMC

Public UI: `https://fans.nas.ace`

Upstream baseline: Rocketplanner83/truefan commit `50973eb89825c7bb7a32165d8d8a8e8afa7ebd59`

## Objective

Extend TrueFan with a pluggable AST2600/IPMI backend and a TrueNAS temperature source, then ship it as a lightweight TrueNAS Custom App. Preserve TrueFan's monitoring-first dashboard and split control-agent architecture. The existing off-box `nas-fan-watch.sh` remains the final fail-safe authority.

## Non-goals

- Do not replace or weaken the external watchdog.
- Do not require privileged containers, host `/sys` write mounts, Docker socket access, or host networking.
- Do not expose BMC, TrueNAS, or agent credentials to browser JavaScript.
- Do not add a database or framework beyond the existing Flask/FastAPI shape.
- Do not run arbitrary shell strings. All `ipmitool` execution uses argv lists with `shell=False`.

## Existing behavior to preserve

- Monitoring-only degradation when the control agent is unavailable.
- Token-protected control-agent API.
- Existing hwmon/PWM backend remains supported and is the default unless configured otherwise.
- Existing endpoints remain compatible: `/`, `/api`, `/status`, `/sensors`, `/pwm/<value>`, `/set/<profile>`.
- `/status` must include both the current nested structure and compatibility top-level fields (`profile`, `uptime`, `load`, `pwm`) so the upstream test/client contract remains valid.

## Runtime topology

One image, two services selected by `TRUEFAN_COMPONENT`:

1. `truefan-core`
   - Gunicorn/Flask on `0.0.0.0:5002`.
   - Public monitoring dashboard.
   - Calls the agent over the private compose network using configurable `CONTROL_AGENT_URL`.
   - Reads the agent token from `CONTROL_AGENT_TOKEN_FILE`.
   - Verifies browser write authorization using a separate `TRUEFAN_UI_WRITE_TOKEN_FILE`.

2. `truefan-control`
   - Uvicorn/FastAPI on `0.0.0.0:5088`, not host-published.
   - Backend selected by `TRUEFAN_BACKEND=ast2600_ipmi|hwmon_pwm`.
   - Reads credentials only from mounted files.
   - Performs all BMC writes and TrueNAS/BMC reads.

Fleet NAS host mapping: `30082:5002`. No agent port is published.

## Secret contract

Supported `*_FILE` variables:

- `TRUEFAN_AGENT_SECRET_FILE`
- `CONTROL_AGENT_TOKEN_FILE`
- `TRUEFAN_UI_WRITE_TOKEN_FILE`
- `BMC_USER_FILE`
- `BMC_PASSWORD_FILE`
- `TRUENAS_USER_FILE`
- `TRUENAS_PASSWORD_FILE`

Files are mounted read-only beneath `/run/secrets`. Missing required secrets fail closed at startup. Secret values must never appear in logs, status JSON, exception text, image layers, compose files, or tests.

## Backend interface

Create a small backend contract rather than branching throughout request handlers:

```python
class FanBackend(Protocol):
    def status(self) -> BackendStatus: ...
    def set_duty_percent(self, percent: int) -> BackendStatus: ...
    def set_auto(self) -> BackendStatus: ...
```

`BackendStatus` must include:

- `backend`
- `mode`: `manual|auto|unknown`
- `duty_percent`: integer or `None`
- `pwm`: integer 0-255 or `None`
- `fan_rpms`: mapping
- `temperatures`: `{cpu_c, board_c, drives_c, max_drive_c, nvme_c}`
- `sensor_ok`
- safe `error` code/message without secrets

Keep `hwmon_pwm` behavior behind its own adapter.

## AST2600/IPMI contract

Connection is LAN IPMI (`ipmitool -I lanplus -H <host> -U <user> -E`) with password supplied through `IPMI_PASSWORD` in the child environment, never `-P` argv and never shell interpolation.

Verified ASRock Rack raw commands:

- Read mode: `raw 0x3a 0xd0 0x12`
- Read duty: `raw 0x3a 0xd0 0x0f`
- Set all 16 channels manual: `raw 0x3a 0xd0 0x11` + sixteen `0x2`
- Set all 16 channels duty: `raw 0x3a 0xd0 0x0e` + sixteen duty bytes
- Set all 16 channels auto: `raw 0x3a 0xd0 0x11` + sixteen `0x0`

Duty values are percentages 0-100 represented as byte values; clamp the user-controllable range to 22-100. Every mutation must read back mode and duty and fail if they do not match.

## Sensor sources

### BMC Redfish

- URL: `https://<BMC_HOST>/redfish/v1/Chassis/Self/Thermal`
- Basic auth from mounted BMC credentials.
- TLS verification configurable; Fleet NAS uses its self-signed BMC certificate with verification disabled explicitly.
- Parse `TEMP_CPU`, board/chipset temperatures, and every fan `Name`/`Reading` RPM.
- Timeouts on every request.

### TrueNAS WebSocket JSON-RPC

- URL: `wss://<TRUENAS_HOST>/api/current`
- Authenticate with `auth.login_ex` / `PASSWORD_PLAIN` using mounted credentials.
- Call `disk.temperatures` with `[[]]`.
- Return every drive temperature, max HDD temperature, and NVMe temperature separately.
- Validate auth response and JSON-RPC errors; never reinterpret failure as `0°C`.
- Timeouts on connect/read; no unbounded retry inside an HTTP request.

## Safety contract

The agent runs a small policy loop and persists only non-secret override state atomically beneath `/data/control-state.json`.

Thresholds, matching the external watchdog:

- Hot: max HDD `>44°C` or CPU `>70°C` → force 100%.
- Cooling band: previous hot incident and max HDD `41-44°C` or CPU `61-70°C` → 50%.
- Recovered: max HDD `<=40°C` and CPU `<=60°C` → quiet 22%.
- Sensor failure: fail closed. Never lower duty; permit only a request that raises/holds the known current duty. If current duty is unknown, command 100%.

Manual control:

- UI requests duty in percent, not raw PWM.
- Accepted range is 22-100%.
- Each override has a TTL, default 300 seconds, maximum 900 seconds.
- Override expiry is persisted as epoch time and re-evaluated after restart.
- Hot/sensor-failure policy overrides any manual request.
- A low-duty request during a hot incident returns HTTP 409 with `safety_locked`; no write occurs.
- Profiles are one-shot TTL overrides: Quiet 22%, Cooling 50%, Emergency 100%.
- Every mutation returns requested duty, effective duty, reason, mode, and read-back status.

The external Mac Studio watchdog remains enabled and may overwrite app settings. The UI must label it as the final authority.

## Core API and browser auth

- Monitoring routes remain unauthenticated on the LAN.
- Browser mutations require `Authorization: Bearer <UI write token>`.
- The UI asks for the control token and stores it in `sessionStorage` only; never render it into HTML or persist it server-side beyond the mounted secret.
- The core validates the UI token with constant-time comparison, then calls the agent using its separate agent token.
- Add `POST /api/control` body `{duty_percent, ttl_seconds}`.
- Add `POST /api/profile/<quiet|cooling|emergency>` body `{ttl_seconds}`.
- Existing `/pwm/<value>` remains compatible by converting 0-255 to percent, but is subject to the same safety/auth checks.
- API errors are structured `{ok:false,error:{code,message},data:null}`.

## Dashboard requirements

Dark, polished, responsive at 390px and desktop. Do not preserve the current placeholder UI.

Display:

- Current safety state: normal/cooling/hot/sensor-failure.
- Current BMC mode and verified duty percent.
- Max HDD, CPU, board, and NVMe temperatures.
- Per-drive temperature table/grid, hottest first, with threshold colors.
- Every populated fan RPM.
- Control-agent online/degraded state and last refresh.
- Explicit badge: `External watchdog: authoritative`.
- Profiles and 22-100% slider with TTL selection.
- Controls visibly lock with the server-provided safety reason.
- Control actions show requested versus effective duty and read-back result.
- Monitoring continues when mutation auth is absent.
- No horizontal overflow at 390 CSS pixels.

## Container/image contract

- Base on current supported Debian slim or Python slim, not Debian bullseye.
- Install `ipmitool`, `smartmontools`, and required Python packages.
- Pin Python dependencies in a requirements file.
- One image supports both components through `TRUEFAN_COMPONENT`.
- Run as non-root.
- Add health checks for core `/status` and agent `/status` using the token-aware path.
- Add a compose example for AST2600 with no literal secrets.
- Add a GitHub Actions workflow to build `linux/amd64` and publish `ghcr.io/kyzcreig/truefan` with immutable SHA tags and a branch tag. Use the repository `GITHUB_TOKEN`; no custom registry secret.

## Tests — strict TDD

Write each failing test before production code and record RED output in the Codex log.

Required coverage:

1. PWM↔percent mapping and 22-100 clamp.
2. Exact AST2600 command argv for read/manual/duty/auto; no shell execution.
3. Password passed through environment rather than argv.
4. Read-back mismatch fails the mutation.
5. Redfish parsing of CPU/board/fan RPM payloads.
6. TrueNAS WS auth success/failure, JSON-RPC errors, drive max/NVMe separation.
7. Hot policy forces 100%; cooling band 50%; recovered 22%.
8. Sensor failure never lowers duty and commands 100% when current duty is unknown.
9. Manual TTL bounds, persistence round-trip, and expired-override restart behavior.
10. Low-duty request while hot returns 409 and performs no low-duty write.
11. Agent token required on status/control endpoints.
12. Core UI token is distinct from agent token and required for mutations.
13. Monitoring degrades honestly if agent is unavailable.
14. `/status` backward-compatible top-level fields plus new structured fields.
15. Static/UI contract checks for percent labels, watchdog badge, controls, and mobile viewport.
16. Existing upstream tests, corrected by implementation rather than deletion.

Test command: `python3 -m pytest -q`.

## Verification gates after unit green

- Build image for `linux/amd64`.
- Start both components locally with fake backends; exercise status/auth/control over HTTP.
- Deploy on TrueNAS via WS `app.create` custom compose.
- Verify agent reads live BMC/TrueNAS data.
- Verify read-only status has real drive temperatures and real RPMs.
- Verify a reversible 100% → current-policy write and IPMI read-back.
- Prove a low request is rejected while a synthetic/live hot condition is active without lowering real fan duty.
- Restart the app and prove status/control state survives appropriately.
- Stop the app and prove the external watchdog remains enabled and can still set/read duty.
- Restore app and verify healthy.
- Provision `fans.nas.ace` with `upstream_http: true`; browser-verify TLS, layout, data, and controls.
- Verify desktop plus 390px mobile and no horizontal overflow.
- Update index.ace, the Fleet NAS app doc, and `nas-fan-control` skill.

## Rollback

- Stop/delete the TrueNAS custom app.
- External `nas-fan-watch` remains untouched and enabled throughout.
- If app control misbehaves, set BMC to 100% through the existing watchdog/IPMI path, then remove the app.
- Remove only the `fans.nas.ace` route if the app is retired; other `.ace` routes remain untouched.
