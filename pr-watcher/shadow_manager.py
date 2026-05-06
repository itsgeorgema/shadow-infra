"""
shadow_manager.py
Creates and destroys per-PR shadow containers using ephemeral docker-compose files.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Base host port for shadow services. Each PR gets port BASE_PORT + pr_number.
BASE_PORT = 9000

# Directory where temporary compose files are stored.
COMPOSE_DIR = Path(os.getenv("SHADOW_COMPOSE_DIR", "/tmp/shadow-infra"))


def _compose_file_path(pr_number: int) -> Path:
    COMPOSE_DIR.mkdir(parents=True, exist_ok=True)
    return COMPOSE_DIR / f"shadow-{pr_number}.yaml"


def _project_name(pr_number: int) -> str:
    return f"shadow-pr{pr_number}"


def _host_port(pr_number: int) -> int:
    return BASE_PORT + pr_number


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess command, logging output and raising on failure."""
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.stdout:
        logger.debug("stdout: %s", result.stdout.strip())
    if result.stderr:
        logger.debug("stderr: %s", result.stderr.strip())
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command {' '.join(cmd)} failed (exit {result.returncode}):\n"
            f"{result.stderr}"
        )
    return result


def spin_up_shadow(
    pr_number: int,
    image: str,
    container_port: str,
) -> str:
    """
    Write a temporary docker-compose file and start the shadow container.

    Args:
        pr_number:      GitHub PR number (used for unique naming and port allocation).
        image:          Docker image to run (e.g. "my-app:pr-42").
        container_port: The port the container listens on internally.

    Returns:
        The shadow service URL (e.g. "http://localhost:9042").

    Raises:
        RuntimeError: If docker-compose fails to start the container.
    """
    host_port = _host_port(pr_number)
    service_name = f"shadow-pr{pr_number}"
    compose_path = _compose_file_path(pr_number)

    compose_content = {
        "version": "3.8",
        "services": {
            service_name: {
                "image": image,
                "ports": [f"{host_port}:{container_port}"],
                "restart": "no",
                "labels": [
                    "shadow-infra=true",
                    f"shadow-infra.pr={pr_number}",
                ],
                "environment": {
                    "SHADOW_MODE": "true",
                    "PR_NUMBER": str(pr_number),
                },
            }
        },
    }

    compose_yaml = yaml.dump(compose_content, default_flow_style=False)
    compose_path.write_text(compose_yaml)
    logger.info("Wrote compose file to %s", compose_path)

    _run([
        "docker-compose",
        "-f", str(compose_path),
        "-p", _project_name(pr_number),
        "up", "-d", "--pull", "always",
    ])

    shadow_url = f"http://localhost:{host_port}"
    logger.info("Shadow for PR #%d started at %s", pr_number, shadow_url)
    return shadow_url


def tear_down_shadow(pr_number: int) -> None:
    """
    Stop and remove the shadow container for the given PR.

    Args:
        pr_number: GitHub PR number.

    Raises:
        RuntimeError: If docker-compose fails to bring down the container.
    """
    compose_path = _compose_file_path(pr_number)

    if not compose_path.exists():
        logger.warning(
            "No compose file found for PR #%d at %s — already cleaned up?",
            pr_number,
            compose_path,
        )
        return

    _run([
        "docker-compose",
        "-f", str(compose_path),
        "-p", _project_name(pr_number),
        "down", "--remove-orphans",
    ])

    try:
        compose_path.unlink()
    except OSError as exc:
        logger.warning("Could not remove compose file %s: %s", compose_path, exc)

    logger.info("Shadow for PR #%d torn down", pr_number)
