"""
shadow_manager.py
Creates and destroys per-PR shadow Deployments and Services using the Kubernetes API.
Replaces the previous docker-compose/Docker-socket approach so pr-watcher runs on
any K8s node regardless of container runtime.
"""

import logging
import os

from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = logging.getLogger(__name__)

NAMESPACE = os.getenv("SHADOW_NAMESPACE", "shadow-infra")
TRAFFIC_SPLITTER_DEPLOYMENT = os.getenv("TRAFFIC_SPLITTER_DEPLOYMENT", "traffic-splitter")


def _k8s_clients() -> tuple[client.AppsV1Api, client.CoreV1Api]:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        # Fallback for local development with a kubeconfig.
        config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()


def _deployment_name(pr_number: int) -> str:
    return f"shadow-pr{pr_number}"


def spin_up_shadow(pr_number: int, image: str, container_port: str) -> str:
    """
    Create (or replace) a Deployment and ClusterIP Service for the shadow pod.

    Args:
        pr_number:      GitHub PR number — used as the unique resource name suffix.
        image:          Docker image to run (e.g. "ghcr.io/org/app:pr-42").
        container_port: Port the container listens on.

    Returns:
        In-cluster shadow URL (e.g. "http://shadow-pr42:8080").

    Raises:
        ApiException: On unexpected Kubernetes API errors.
    """
    apps_api, core_api = _k8s_clients()
    name = _deployment_name(pr_number)
    port = int(container_port)
    labels = {"app": name, "shadow-infra": "true", "pr": str(pr_number)}

    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE, labels=labels),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(match_labels={"app": name}),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": name}),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name=name,
                            image=image,
                            ports=[client.V1ContainerPort(container_port=port)],
                            env=[
                                client.V1EnvVar(name="SHADOW_MODE", value="true"),
                                client.V1EnvVar(name="PR_NUMBER", value=str(pr_number)),
                            ],
                        )
                    ]
                ),
            ),
        ),
    )

    service = client.V1Service(
        metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE, labels=labels),
        spec=client.V1ServiceSpec(
            selector={"app": name},
            ports=[client.V1ServicePort(port=port, target_port=port)],
            type="ClusterIP",
        ),
    )

    # Upsert Deployment — replace if it already exists (e.g. synchronize event).
    try:
        apps_api.read_namespaced_deployment(name=name, namespace=NAMESPACE)
        apps_api.replace_namespaced_deployment(name=name, namespace=NAMESPACE, body=deployment)
        logger.info("Replaced Deployment %s in namespace %s", name, NAMESPACE)
    except ApiException as exc:
        if exc.status == 404:
            apps_api.create_namespaced_deployment(namespace=NAMESPACE, body=deployment)
            logger.info("Created Deployment %s in namespace %s", name, NAMESPACE)
        else:
            raise

    # Upsert Service — create only; selector is stable across synchronize events.
    try:
        core_api.read_namespaced_service(name=name, namespace=NAMESPACE)
        logger.info("Service %s already exists — reusing", name)
    except ApiException as exc:
        if exc.status == 404:
            core_api.create_namespaced_service(namespace=NAMESPACE, body=service)
            logger.info("Created Service %s in namespace %s", name, NAMESPACE)
        else:
            raise

    shadow_url = f"http://{name}:{port}"
    logger.info("Shadow for PR #%d live at %s", pr_number, shadow_url)
    return shadow_url


def tear_down_shadow(pr_number: int) -> None:
    """
    Delete the Deployment and Service for the given PR.

    Args:
        pr_number: GitHub PR number.

    Raises:
        RuntimeError: If deletion fails for a reason other than 404.
    """
    apps_api, core_api = _k8s_clients()
    name = _deployment_name(pr_number)

    for label, delete_fn in [
        ("Deployment", lambda: apps_api.delete_namespaced_deployment(
            name=name, namespace=NAMESPACE,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )),
        ("Service", lambda: core_api.delete_namespaced_service(
            name=name, namespace=NAMESPACE,
        )),
    ]:
        try:
            delete_fn()
            logger.info("Deleted %s %s", label, name)
        except ApiException as exc:
            if exc.status == 404:
                logger.warning("%s %s not found — already deleted?", label, name)
            else:
                raise RuntimeError(f"Failed to delete {label} {name}: {exc}") from exc

    logger.info("Shadow for PR #%d torn down", pr_number)


def patch_traffic_splitter(shadow_url: str, deployment_id: str) -> None:
    """
    Patch the traffic-splitter Deployment env vars so it routes shadow traffic
    to the newly created (or cleared) shadow target.

    Setting shadow_url="" disables shadowing until the next PR is activated.
    """
    apps_api, _ = _k8s_clients()
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "traffic-splitter",
                        "env": [
                            {"name": "SHADOW_URL", "value": shadow_url},
                            {"name": "DEPLOYMENT_ID", "value": deployment_id},
                        ],
                    }]
                }
            }
        }
    }
    apps_api.patch_namespaced_deployment(
        name=TRAFFIC_SPLITTER_DEPLOYMENT,
        namespace=NAMESPACE,
        body=patch,
    )
    logger.info(
        "Patched %s: SHADOW_URL=%r DEPLOYMENT_ID=%r",
        TRAFFIC_SPLITTER_DEPLOYMENT, shadow_url, deployment_id,
    )


def clear_traffic_splitter() -> None:
    """Remove the active shadow target from the traffic-splitter (PR closed)."""
    patch_traffic_splitter(shadow_url="", deployment_id="")
