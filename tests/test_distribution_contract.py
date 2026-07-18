from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_one_non_root_current_image_contains_both_components_and_tools():
    dockerfile = read("Dockerfile")
    entrypoint = read("entrypoint.sh")

    assert "bullseye" not in dockerfile.lower()
    assert "python:3.13-slim-bookworm" in dockerfile
    assert "ipmitool" in dockerfile
    assert "smartmontools" in dockerfile
    assert "requirements.txt" in dockerfile
    assert "USER truefan" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "TRUEFAN_COMPONENT" in entrypoint
    assert "truefan-core" in entrypoint
    assert "truefan-control" in entrypoint
    assert "gunicorn" in entrypoint
    assert "uvicorn" in entrypoint
    assert "eval " not in entrypoint


def test_runtime_dependencies_are_pinned():
    requirements = [
        line.strip()
        for line in read("requirements.txt").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    assert requirements
    assert all("==" in line for line in requirements)
    assert any(line.lower().startswith("flask==") for line in requirements)
    assert any(line.lower().startswith("fastapi==") for line in requirements)
    assert any(line.lower().startswith("websocket-client==") for line in requirements)


def test_ast2600_compose_has_no_literal_secrets_or_published_agent_port():
    compose = read("docker-compose.yaml")

    assert "truefan-core:" in compose
    assert "truefan-control:" in compose
    assert '"30082:5002"' in compose
    control_block = compose.rsplit("\n  truefan-control:", 1)[1].split("\nvolumes:", 1)[0]
    assert "ports:" not in control_block
    for variable in (
        "TRUEFAN_AGENT_SECRET_FILE",
        "CONTROL_AGENT_TOKEN_FILE",
        "TRUEFAN_UI_WRITE_TOKEN_FILE",
        "BMC_USER_FILE",
        "BMC_PASSWORD_FILE",
        "TRUENAS_USER_FILE",
        "TRUENAS_PASSWORD_FILE",
    ):
        assert variable in compose
    assert "privileged:" not in compose
    assert "network_mode: host" not in compose
    assert "password:" not in compose.lower()


def test_github_image_workflow_uses_sha_branch_tags_and_github_token():
    workflow = read(".github/workflows/image.yml")

    assert "linux/amd64" in workflow
    assert "ghcr.io/kyzcreig/truefan" in workflow
    assert "type=sha" in workflow
    assert "type=ref,event=branch" in workflow
    assert "secrets.GITHUB_TOKEN" in workflow
    assert "packages: write" in workflow


def test_docker_context_excludes_repository_metadata_tests_and_bytecode():
    dockerignore = read(".dockerignore")

    for entry in (".git", ".pytest_cache", "**/__pycache__", "tests", "docs", ".env"):
        assert entry in dockerignore
