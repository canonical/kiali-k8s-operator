#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import pytest
import requests
import yaml
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import get_k8s_service_ip

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
resources = {
    "kiali-image": METADATA["resources"]["kiali-image"]["upstream-source"],
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


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, charm_under_test):
    """Build the charm_under_test and deploy it."""
    await ops_test.model.deploy(
        charm_under_test, resources=resources, application_name=APP_NAME, trust=True
    )

    # Charm will be blocked because it needs prometheus
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=1000)


@pytest.mark.setup
@pytest.mark.dependency
@pytest.mark.abort_on_fail
async def test_deploy_dependencies(ops_test: OpsTest):
    """Deploy the integration test dependencies."""
    await ops_test.model.deploy(**asdict(ISTIO_K8S))
    await ops_test.model.deploy(**asdict(PROMETHEUS_K8S))
    await ops_test.model.deploy(**asdict(ISTIO_INGRESS_K8S))

    await ops_test.model.add_relation(ISTIO_K8S.application_name, PROMETHEUS_K8S.application_name)

    await ops_test.model.wait_for_idle(
        apps=[ISTIO_K8S.application_name, PROMETHEUS_K8S.application_name],
        status="active",
        raise_on_blocked=True,
        timeout=1000,
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_add_relation_prometheus(ops_test: OpsTest):
    """Relate the charm_under_test to prometheus."""
    await ops_test.model.add_relation(
        f"{APP_NAME}:prometheus", f"{PROMETHEUS_K8S.application_name}:grafana-source"
    )
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=60)


@pytest.mark.abort_on_fail
async def test_kiali_is_available(ops_test: OpsTest):
    """Assert that Kiali is up and available inside the cluster."""
    # Arrange - get the Kiali service IP
    kiali_service_ip = get_k8s_service_ip(ops_test.model.name, APP_NAME)

    # Assert that Kiali is available via the charm's service
    assert kiali_service_ip is not None, "Kiali service IP not found"
    resp = requests.get(url=f"http://{kiali_service_ip}:20001/kiali")
    assert resp.status_code == 200, "Kiali is not available"


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_ingress_relation(ops_test: OpsTest):
    """Relate kiali to istio-ingress."""
    await ops_test.model.add_relation(ISTIO_INGRESS_K8S.application_name, APP_NAME)

    await ops_test.model.wait_for_idle(
        apps=[ISTIO_INGRESS_K8S.application_name, APP_NAME],
        status="active",
        raise_on_blocked=True,
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_ingress_is_available(ops_test: OpsTest):
    """Assert that Kiali is exposed correctly and available via the ingress url."""
    # Arrange - get the Ingress IP
    ingress_ip = get_k8s_service_ip(
        ops_test.model.name, f"{ISTIO_INGRESS_K8S.application_name}-istio"
    )

    # Assert that Kiali is available via the ingress service
    assert ingress_ip is not None, "Ingress IP not found"
    resp = requests.get(url=f"http://{ingress_ip}/{ops_test.model.name}-{APP_NAME}")
    assert resp.status_code == 200, "Kiali is not available"


@pytest.mark.teardown
async def test_remove_relation_prometheus(ops_test: OpsTest):
    """Assert charm is blocked when we remove the prometheus relation."""
    await ops_test.model.applications[PROMETHEUS_K8S.application_name].remove_relation(
        f"{APP_NAME}:prometheus", PROMETHEUS_K8S.application_name
    )
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="blocked", timeout=1000)
