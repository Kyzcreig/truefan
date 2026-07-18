# TrueFan

TrueFan is a monitoring-first NAS fan controller. It supports the original Linux `hwmon_pwm` backend and an ASRock Rack AST2600 `ast2600_ipmi` backend, while keeping hardware access in a private control agent. If that agent is unavailable, the public dashboard continues in honest monitoring-only mode.

The Fleet NAS target is TrueNAS SCALE with an AST2600 BMC. The off-box **External watchdog remains authoritative**: it is not installed, disabled, or modified by this project and may overwrite an app setting at any time.

## Architecture

One non-root image runs one of two components selected by `TRUEFAN_COMPONENT`:

- `truefan-core` serves Gunicorn/Flask on port 5002. Monitoring routes are unauthenticated; browser mutations require a UI write token. The core reaches the agent with a different control-agent token.
- `truefan-control` serves Uvicorn/FastAPI on port 5088. It owns BMC/TrueNAS reads, fan writes, the safety loop, and `/data/control-state.json`. The compose example does not publish this port.

The AST2600 backend uses LAN IPMI with the password placed only in the child process environment. BMC thermal/fan telemetry comes from Redfish, and per-drive temperatures come from the TrueNAS WebSocket JSON-RPC API. All network operations are bounded by timeouts.

## Safety behavior

Safety takes priority over manual requests and persisted overrides:

| State | Trigger | Effective policy |
| --- | --- | --- |
| Hot | Max HDD above 44°C or CPU above 70°C | Force 100% |
| Cooling | A prior hot incident has not recovered | At least 50% |
| Recovered/normal | Max HDD at or below 40°C and CPU at or below 60°C | Quiet 22% unless an unexpired safe override applies |
| Sensor failure | Either required source fails | Never lower known duty; use 100% if current duty is unknown |

Manual duty is 22–100%. Overrides default to 300 seconds and accept 1–900 seconds. Quiet, Cooling, and Emergency are one-shot overrides at 22%, 50%, and 100%. A low request during a hot incident returns `409 safety_locked` without a low-duty write. Every accepted mutation reports requested duty, effective duty, reason, mode, and verified read-back.

Override expiry and prior-hot state are written atomically to `/data/control-state.json`. No credentials are persisted there.

## Credentials

Credentials must be non-empty, read-only files. Do not place secret values in compose YAML, environment values, image build arguments, or the repository.

| Component | Variable | Contents |
| --- | --- | --- |
| Agent | `TRUEFAN_AGENT_SECRET_FILE` | Bearer token accepted by the agent |
| Core | `CONTROL_AGENT_TOKEN_FILE` | Same agent bearer token, mounted separately |
| Core | `TRUEFAN_UI_WRITE_TOKEN_FILE` | Distinct token entered by dashboard users |
| Agent | `BMC_USER_FILE` | BMC/IPMI user |
| Agent | `BMC_PASSWORD_FILE` | BMC/IPMI password |
| Agent | `TRUENAS_USER_FILE` | Read-capable TrueNAS API user |
| Agent | `TRUENAS_PASSWORD_FILE` | TrueNAS API password |

The dashboard keeps the UI write token only in `sessionStorage`, so closing the tab clears it. The agent token is never sent to browser JavaScript.

## AST2600 compose deployment

The included [docker-compose.yaml](docker-compose.yaml) maps `30082:5002`, leaves the agent private, drops all Linux capabilities, and uses no host networking, privileged mode, Docker socket, or host `/sys` write mount.

1. Create seven local secret files outside the repository with restrictive permissions. The agent token path is supplied to both services by compose, while the UI token must contain a different value.
2. Export the two non-secret host values and the seven secret-file paths expected at the bottom of `docker-compose.yaml`:

   ```sh
   export BMC_HOST=bmc.example.internal
   export TRUENAS_HOST=nas.example.internal
   export TRUEFAN_AGENT_SECRET_PATH=/secure/path/agent-token
   export TRUEFAN_UI_WRITE_TOKEN_PATH=/secure/path/ui-write-token
   export BMC_USER_PATH=/secure/path/bmc-user
   export BMC_PASSWORD_PATH=/secure/path/bmc-pass
   export TRUENAS_USER_PATH=/secure/path/truenas-user
   export TRUENAS_PASSWORD_PATH=/secure/path/truenas-pass
   ```

3. Review TLS settings. The Fleet example explicitly sets both `BMC_TLS_VERIFY: "false"` and `TRUENAS_TLS_VERIFY: "false"` because those private-IP appliance endpoints use locally generated certificates. Generic installs default to verification enabled; prefer installing the relevant CA and enabling verification when possible.
4. Build and start:

   ```sh
   docker compose config
   docker compose build
   docker compose up -d
   ```

5. Open `http://<nas-address>:30082`. Confirm the agent is online, real drive temperatures and every populated fan RPM appear, and the mode/duty read-back is plausible before entering the separate UI token.

The GitHub Actions workflow publishes `linux/amd64` images to `ghcr.io/kyzcreig/truefan` with immutable SHA and branch tags using the repository `GITHUB_TOKEN`.

## `hwmon_pwm` backend

`hwmon_pwm` remains the default backend. Select it with `TRUEFAN_BACKEND=hwmon_pwm` on the control component. A deployment must explicitly mount only the required hwmon paths and grant the non-root `truefan` user access to the intended PWM files. Do not copy the AST2600 compose example blindly for hwmon: it intentionally has no host hwmon mount.

The same fail-closed policy applies. Required CPU and drive temperature inputs must be visible to the agent; otherwise it treats the condition as a sensor failure rather than as `0°C`.

## APIs and authentication

Monitoring endpoints remain compatible and LAN-readable:

- `GET /`, `/api`, `/status`, `/sensors`
- `/status` includes legacy top-level `profile`, `uptime`, `load`, and `pwm`, plus structured `backend`, `safety`, drive, fan, agent, and control fields.

Core mutations use `Authorization: Bearer <UI write token>`:

- `POST /api/control` with `{"duty_percent": 50, "ttl_seconds": 300}`
- `POST /api/profile/<quiet|cooling|emergency>` with `{"ttl_seconds": 300}`
- Compatibility routes `POST /pwm/<0-255>` and `POST /set/<profile>` pass through the same authorization and safety checks.

Agent `/status`, `/control`, `/set_pwm`, and `/profile/<profile>` require the separate agent token. API failures use `{"ok":false,"error":{"code":"…","message":"…"},"data":null}`.

## Development and verification

Install pinned runtime dependencies in an isolated environment, then run:

```sh
python3 -m pip install -r requirements.txt
python3 -m pytest -q
python3 -m compileall -q app truefan_control tests
git diff --check
docker build --platform linux/amd64 -t truefan:local .
```

Tests use fakes for IPMI, Redfish, TrueNAS, backends, and HTTP boundaries. They require no network, BMC, TrueNAS host, Docker daemon, or real credentials. Strict RED/GREEN evidence is in [docs/tdd-evidence.md](docs/tdd-evidence.md).

## Rollback

Stop or delete only the TrueFan custom app. The external watchdog must remain enabled throughout. If app control is suspect, use the established external watchdog/IPMI path to command 100%, verify read-back, and then remove the app. Retiring the app should remove only its own public route; it must not alter other routes or services.

Live TrueNAS deployment, BMC writes, watchdog exercises, DNS/frontdoor changes, and external documentation systems are deliberately not performed by repository tests or builds.
