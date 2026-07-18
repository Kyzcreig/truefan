# TDD evidence

All commands were run from the repository root. Outputs are intentionally concise; the full final verification output is recorded at the end of this file.

## Baseline — known upstream compatibility failure

RED, before implementation:

```text
$ python3 -m pytest -q
..F                                                                      [100%]
FAILED tests/test_sensors.py::test_status_endpoint_returns_valid_json
1 failed, 2 passed in 0.17s
```

The failure is the approved baseline: `/status` omitted the compatibility top-level `profile`, `uptime`, and `load` fields while retaining them beneath `system`.

## Cluster 1 — PWM mapping and AST2600/IPMI command safety

RED, after adding command/mapping/read-back tests and before adding the backend package:

```text
$ python3 -m pytest -q tests/test_ast2600_backend.py
E   ModuleNotFoundError: No module named 'truefan_control'
1 error in 0.07s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_ast2600_backend.py
.....                                                                    [100%]
5 passed in 0.01s
```

## Cluster 2 — Redfish and TrueNAS WebSocket sensors

RED, after adding parser/auth/RPC/separation tests and before adding the clients:

```text
$ python3 -m pytest -q tests/test_sensor_clients.py
E   ModuleNotFoundError: No module named 'truefan_control.redfish'
1 error in 0.09s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_sensor_clients.py
....                                                                     [100%]
4 passed in 0.01s
```

## Cluster 3 — fail-closed policy, TTL overrides, and atomic state

RED, after adding transition/failure/persistence/no-write tests and before adding policy/service modules:

```text
$ python3 -m pytest -q tests/test_safety_policy.py
E   ModuleNotFoundError: No module named 'truefan_control.policy'
1 error in 0.21s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_safety_policy.py
.....                                                                    [100%]
5 passed in 0.03s
```

## Cluster 4 — backend adapters, file secrets, and authenticated agent API

RED, after adding adapter/config/API tests:

```text
$ python3 -m pytest -q tests/test_agent_api.py tests/test_backend_adapters.py
E   ModuleNotFoundError: No module named 'truefan_control.main'
E   ImportError: cannot import name 'Ast2600Backend'
2 errors in 1.42s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_agent_api.py tests/test_backend_adapters.py
.......                                                                  [100%]
7 passed in 2.64s
```

## Cluster 5 — core compatibility, degradation, and separate browser auth

RED, after adding structured/legacy/degradation/distinct-token tests:

```text
$ python3 -m pytest -q tests/test_core_api.py tests/test_sensors.py
FFFF..F                                                                  [100%]
5 failed, 2 passed in 1.39s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_core_api.py tests/test_sensors.py
.......                                                                  [100%]
7 passed in 1.00s
```

## Cluster 6 — dashboard and distribution contracts

RED, after adding static mobile/auth/control and image/compose/workflow tests:

```text
$ python3 -m pytest -q tests/test_ui_contract.py tests/test_distribution_contract.py
FFFFFFF                                                                  [100%]
7 failed in 0.13s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_ui_contract.py tests/test_distribution_contract.py
.......                                                                  [100%]
7 passed in 0.01s
```

## Cluster 7 — operator documentation

RED, after adding an operator-guide contract test and before replacing the upstream README:

```text
$ python3 -m pytest -q tests/test_docs_contract.py
F                                                                        [100%]
1 failed in 0.02s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_docs_contract.py
.                                                                        [100%]
1 passed in 0.05s
```

## Cluster 8 — integration audit corrections

RED, after adding a TrueNAS `login_ex` success-object case and an explicit shell-disable check for the retained SMART boundary:

```text
$ python3 -m pytest -q tests/test_sensor_clients.py::test_truenas_login_ex_accepts_explicit_success_response tests/test_sensors.py::test_smartctl_subprocess_is_an_argv_list_with_shell_disabled
FF                                                                       [100%]
2 failed in 1.06s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_sensor_clients.py::test_truenas_login_ex_accepts_explicit_success_response tests/test_sensors.py::test_smartctl_subprocess_is_an_argv_list_with_shell_disabled
..                                                                       [100%]
2 passed in 0.08s
```

## Cluster 9 — visual smoke correction

RED, after the first Chrome smoke test exposed `null%` and machine-formatted safety reasons:

```text
$ python3 -m pytest -q tests/test_ui_contract.py::test_dashboard_formats_missing_values_and_machine_reasons_for_people
F                                                                        [100%]
1 failed in 0.03s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_distribution_contract.py::test_docker_context_excludes_repository_metadata_tests_and_bytecode
.                                                                        [100%]
1 passed in 0.01s
```

## Final verification

```text
$ python3 -m pytest -q
..........................................                               [100%]
42 passed in 3.38s

$ ruff check app truefan_control tests healthcheck.py
All checks passed!

$ python3 -m compileall -q app truefan_control tests
(no output; exit 0)

$ node --check app/static/js/dashboard.js
(no output; exit 0)

$ git diff --check
(no output; exit 0)

$ docker compose config --quiet  # non-secret placeholder hosts/paths
(no output; exit 0)

$ docker build --platform linux/amd64 -t truefan:ast2600-test .
#15 naming to docker.io/library/truefan:ast2600-test done
#15 DONE 0.8s

$ docker run --rm --platform linux/amd64 --entrypoint python truefan:ast2600-test -m pip check
No broken requirements found.

$ docker image inspect truefan:ast2600-test ...
platform=linux/amd64 user=truefan entrypoint=["/opt/truefan/entrypoint.sh"] healthcheck=["CMD","python","/opt/truefan/healthcheck.py"]

$ local two-container fake-agent/core exercise
{"agent_available": true, "agent_health": "healthy", "backend": "fake_verification", "control_effective": 60, "control_requested": 60, "core_health": "healthy", "readback_verified": true}

$ Chrome DevTools 390 CSS-pixel overflow check
{"innerWidth":390,"clientWidth":390,"scrollWidth":390,"bodyScrollWidth":390,"mobileMedia":true}
```

Repository high-risk token/private-key pattern scans and image-history credential-keyword scans returned no matches.

Live TrueNAS `app.create`, BMC/TrueNAS reads and writes, watchdog exercise, DNS/frontdoor work, and external documentation updates were not run because the task explicitly prohibits touching those external systems. They remain deployment-time verification gates.

## Cluster 12 — shell-free entrypoint indirection

RED, after adding a distribution check that forbids shell `eval` in secret-file validation:

```text
$ python3 -m pytest -q tests/test_distribution_contract.py::test_one_non_root_current_image_contains_both_components_and_tools
F                                                                        [100%]
1 failed in 0.12s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_distribution_contract.py::test_one_non_root_current_image_contains_both_components_and_tools
.                                                                        [100%]
1 passed in 0.09s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_ui_contract.py::test_dashboard_formats_missing_values_and_machine_reasons_for_people
.                                                                        [100%]
1 passed in 0.00s
```

## Cluster 10 — persisted-state validation and control serialization

RED, after adding tampered-state and concurrent-mutation tests:

```text
$ python3 -m pytest -q tests/test_safety_policy.py::test_invalid_persisted_override_is_discarded tests/test_safety_policy.py::test_service_serializes_policy_and_backend_mutations
FF                                                                       [100%]
2 failed in 0.16s
```

GREEN:

```text
$ python3 -m pytest -q tests/test_safety_policy.py::test_invalid_persisted_override_is_discarded tests/test_safety_policy.py::test_service_serializes_policy_and_backend_mutations
..                                                                       [100%]
2 passed in 0.24s
```

## Cluster 11 — minimal, clean image context

RED, after adding an image-context exclusion test:

```text
$ python3 -m pytest -q tests/test_distribution_contract.py::test_docker_context_excludes_repository_metadata_tests_and_bytecode
F                                                                        [100%]
1 failed in 0.03s
```
