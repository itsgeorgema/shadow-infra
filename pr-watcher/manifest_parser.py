"""
manifest_parser.py
Fetches and parses a docker-compose.yaml from a GitHub PR branch,
returning the primary service image and exposed port.
"""

import base64
import logging
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)


def fetch_docker_compose(
    repo: str,
    branch: str,
    github_token: str,
) -> dict[str, Any]:
    """
    Fetch docker-compose.yaml from the given repo/branch via the GitHub Contents API.

    Args:
        repo:         Full repo name, e.g. "owner/repo".
        branch:       Branch name to read from.
        github_token: Personal access token or GitHub App installation token.

    Returns:
        Parsed docker-compose YAML as a dict.

    Raises:
        ValueError: If the file is not found or cannot be parsed.
    """
    url = f"https://api.github.com/repos/{repo}/contents/docker-compose.yaml"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"ref": branch}

    response = httpx.get(url, headers=headers, params=params, timeout=15)

    if response.status_code == 404:
        raise ValueError(
            f"docker-compose.yaml not found in {repo}@{branch}"
        )
    response.raise_for_status()

    data = response.json()
    if data.get("encoding") != "base64":
        raise ValueError(f"Unexpected encoding: {data.get('encoding')}")

    raw = base64.b64decode(data["content"]).decode("utf-8")
    try:
        compose = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse docker-compose.yaml: {exc}") from exc

    return compose


def extract_primary_service(compose: dict[str, Any]) -> dict[str, str]:
    """
    Identify the first service that exposes a port and return its metadata.

    Args:
        compose: Parsed docker-compose dict.

    Returns:
        A dict with keys: service_name, image, port (host port as string).

    Raises:
        ValueError: If no service with ports is found or image is missing.
    """
    services: dict[str, Any] = compose.get("services", {})
    if not services:
        raise ValueError("docker-compose.yaml contains no services")

    for service_name, service_cfg in services.items():
        ports = service_cfg.get("ports", [])
        if not ports:
            continue

        # Ports can be "host:container" strings or dicts with target/published.
        first_port = ports[0]
        host_port: str | None = None

        if isinstance(first_port, str):
            # Format: "8080:80" or just "80"
            parts = first_port.split(":")
            host_port = parts[0] if len(parts) == 2 else parts[0]
        elif isinstance(first_port, dict):
            published = first_port.get("published")
            if published is not None:
                host_port = str(published)
            else:
                target = first_port.get("target")
                if target is not None:
                    host_port = str(target)
        elif isinstance(first_port, int):
            host_port = str(first_port)

        if host_port is None:
            logger.warning("Could not parse port for service %s, skipping", service_name)
            continue

        image = service_cfg.get("image")
        if not image:
            # Try build context — use service name as a fallback image reference.
            build = service_cfg.get("build")
            if build:
                image = service_name
            else:
                logger.warning("Service %s has no image or build, skipping", service_name)
                continue

        return {
            "service_name": service_name,
            "image": image,
            "port": host_port,
        }

    raise ValueError("No service with exposed ports found in docker-compose.yaml")


def parse_manifest(repo: str, branch: str, github_token: str) -> dict[str, str]:
    """
    High-level helper: fetch and parse the manifest, return primary service info.
    """
    compose = fetch_docker_compose(repo, branch, github_token)
    return extract_primary_service(compose)
