"""Test helpers that should be moved to an external package in future for sharing."""

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import lightkube
import yaml
from lightkube.resources.core_v1 import Service

logger = logging.getLogger(__name__)

app_metadata = yaml.safe_load(Path("./charmcraft.yaml").read_text())
KIALI_NAME = app_metadata["name"]
kiali_resources = {
    "kiali-image": app_metadata["resources"]["kiali-image"]["upstream-source"],
}


@dataclass
class CharmDeploymentConfiguration:
    entity_url: str  # aka charm name or local path to charm
    application_name: str
    channel: str
    trust: bool
    config: Optional[dict] = None


ISTIO_K8S = CharmDeploymentConfiguration(
    entity_url="istio-k8s", application_name="istio-k8s", channel="latest/edge", trust=True
)
ISTIO_INGRESS_K8S = CharmDeploymentConfiguration(
    entity_url="istio-ingress-k8s",
    application_name="istio-ingress-k8s",
    channel="latest/edge",
    trust=True,
)
PROMETHEUS_K8S = CharmDeploymentConfiguration(
    entity_url="prometheus-k8s",
    application_name="prometheus-k8s",
    channel="latest/edge",
    trust=True,
)


def build_charm(
    charm_root_path: Union[str, Path] = "./", from_environment_variable: Optional[str] = None
) -> str:
    """Build a charm and return the path to that charm.

    This function will, in this order:
    - Check if the charm path is specified in an environment variable, and if so return that path
    - Build the charm in the specified path and return the path to the built charm.

    This is to allow for using existing charms built externally, such as in a CI pipeline or when iterating on CI
    changes.

    Args:
        charm_root_path: The path to the charm to build
        from_environment_variable: The name of an environment variable that contains the path to the built charm.  Note
                                   that, if executed behhind tox, this environment variable must also be added to
                                   tox.ini's passenv.
    """
    if from_environment_variable and (charm_path := os.getenv(from_environment_variable)):
        return Path(charm_path).absolute()

    charm_root_path: Path = Path(charm_root_path)
    charm_name = get_charm_name_from_yaml(charm_root_path)

    count = 0
    while True:
        try:
            subprocess.check_call(
                args=["charmcraft", "pack"],
                cwd=charm_root_path,
            )
            break
        except RuntimeError:
            logger.warning("Failed to build charm. Trying again!")
            count += 1

            if count == 3:
                raise

    # Return the charm file produced.  If multiple are produced, return the first one.
    # TODO: Handle this better
    for charm in charm_root_path.glob(f"{charm_name}*.charm"):
        return charm.absolute()
    else:
        raise FileNotFoundError(f"No charm files found in '{charm_root_path}'")


def get_charm_name_from_yaml(charm_root_path: Union[str, Path]) -> str:
    """Return the charm's name as defined in charmcraft.yaml, failing back to metadata.yaml if not available."""
    charm_root_path = Path(charm_root_path)
    charmcraft_path = charm_root_path / "charmcraft.yaml"
    metadata_path = charm_root_path / "metadata.yaml"
    charm_name = None
    if charmcraft_path.exists():
        charmcraft_yaml = yaml.safe_load(charmcraft_path.read_text())
        if "name" in charmcraft_yaml:
            charm_name = charmcraft_yaml["name"]
    if charm_name is None:
        charm_name = yaml.safe_load(metadata_path.read_text())["name"]

    return charm_name


def get_k8s_service_ip(namespace: str, service_name: str) -> Optional[str]:
    """Get the ClusterIP or LoadBalancer IP of a Kubernetes service using lightkube.

    Args:
        namespace: The namespace of the Kubernetes service.
        service_name: The name of the Kubernetes service.

    Returns:
        The LoadBalancer IP if the service type is LoadBalancer and has an IP,
        otherwise the ClusterIP. Returns None if the service is not found or no IP is available.
    """
    try:
        c = lightkube.Client()
        svc = c.get(Service, namespace=namespace, name=service_name)

        if svc.spec.type == "LoadBalancer":
            ingress = svc.status.loadBalancer.ingress
            if ingress and len(ingress) > 0 and hasattr(ingress[0], "ip"):
                return ingress[0].ip

        return svc.spec.clusterIP

    except Exception as e:
        logger.error("Error retrieving service address %s", e, exc_info=1)
        return None
