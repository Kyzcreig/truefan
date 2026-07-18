# External Code Ingest Audit — TrueFan

Date: 2026-07-18

Source: <https://github.com/Rocketplanner83/truefan>

Audited commit: `50973eb89825c7bb7a32165d8d8a8e8afa7ebd59`

License: MIT

## Automated scan

Command:

```bash
~/.hermes/skills-shared/security/external-code-ingest-audit/scripts/audit.sh \
  --repo /tmp/truefan-review.x35uuh \
  --output-dir /tmp/truefan-audit \
  --json
```

Result: PASS

- High findings: 0
- Medium findings: 1
- Medium finding: credential-file-read pattern in `.gitignore`; manually classified as benign because it is an ignore rule, not executable credential access.

## Manual review

Reviewed before adaptation:

- `Dockerfile`, `entrypoint.sh`, `.dockerignore`
- `.github/workflows/*`
- `app/server.py`, `app/control.py`, `app/control_client.py`
- `app/sensors.py`, `app/temperature_sources.py`, `app/fan.py`
- `app/templates/index.html`, dashboard/control JavaScript, CSS
- `truefan-control/main.py`, `truefan-control/pwm.py`, `truefan-control/hwmon.py`, `truefan-control/security.py`
- Compose files and tests

## Relevant findings

- Existing split architecture is sound: monitoring core plus token-protected local control agent.
- Existing control backend writes Linux hwmon PWM files and does not support BMC-owned AST2600 fan headers.
- Existing image is non-root but uses an obsolete Debian bullseye base and unpinned `pip install` dependencies.
- Existing public repository workflows contain no deployment credentials.
- Existing agent token comparison uses constant-time `secrets.compare_digest`.
- Existing core degrades to monitoring-only when the agent is unavailable.
- Existing UI is a minimal monitor and currently exposes no functional authenticated controls.
- Existing test baseline is red at the audited commit: `2 passed, 1 failed` because `/status` moved profile/uptime/load under `system` while an upstream test still requires top-level compatibility fields.

## Adaptation decision

Safe to adapt on a fleet-owned fork with these constraints:

- Add an explicit AST2600/IPMI backend; do not replace hwmon behavior.
- Pass the IPMI password through child environment, never argv.
- Mount BMC/TrueNAS/control secrets as read-only files; never bake them into the image.
- Keep the control agent unexposed outside the compose network.
- Require separate UI-write and agent tokens.
- Retain the off-box watchdog as authoritative fail-safe.
- Build and deploy only from the fleet fork after tests and image scan pass.

The implementation contract is `docs/ast2600-truenas-build-spec.md`.
