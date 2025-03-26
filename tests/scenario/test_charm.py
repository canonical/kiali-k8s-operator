#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from contextlib import nullcontext as does_not_raise
from unittest.mock import patch

import pytest
from ops import ActiveStatus, BlockedStatus, WaitingStatus
from scenario import Container, Relation, State

from exceptions import ConfigurationBlockingError

REMOTE_PROMETHEUS_APP_NAME = "grafana"
REMOTE_PROMETHEUS_MODEL = "some-model"
REMOTE_PROMETHEUS_MODEL_UUID = "1"
REMOTE_PROMETHEUS_TYPE = "prometheus"
REMOTE_PROMETHEUS_URL = "http://prometheus:9090"


def mock_prometheus_relation() -> Relation:
    """Return a mock relation to prometheus."""
    return Relation(
        endpoint="prometheus",
        interface="prometheus_api",
        remote_app_name=REMOTE_PROMETHEUS_APP_NAME,
        remote_app_data={
            "direct_url": REMOTE_PROMETHEUS_URL,
        },
    )


@pytest.mark.parametrize(
    "container, relations, kiali_available, expected_status",
    [
        (
            # Has all inputs - Active
            Container(name="kiali", can_connect=True),
            [
                mock_prometheus_relation(),
            ],
            True,
            ActiveStatus,
        ),
        (
            # Inactive - container not ready
            Container(name="kiali", can_connect=False),
            [
                mock_prometheus_relation(),
            ],
            False,
            WaitingStatus,
        ),
        (
            # Inactive - prometheus relation not ready
            Container(name="kiali", can_connect=True),
            [],
            False,
            BlockedStatus,
        ),
        (
            # Inactive - inputs ready, but kiali not available
            Container(name="kiali", can_connect=True),
            [
                mock_prometheus_relation(),
            ],
            False,
            WaitingStatus,
        ),
    ],
)
def test_charm_given_inputs(
    this_charm_context, container, relations, kiali_available, expected_status
):
    """Tests that the charm responds as expected to standard inputs."""
    # Arrange
    state = State(
        containers=[container],
        relations=relations,
        leader=True,
    )

    with patch("charm._is_kiali_available", lambda x: kiali_available):
        out = this_charm_context.run(this_charm_context.on.config_changed(), state)

    assert isinstance(out.unit_status, expected_status)


@pytest.mark.parametrize(
    "prometheus_url, expected, expected_context",
    [
        (
            # Active: All inputs provided.
            "http://prometheus:9090",
            {
                "auth": {"strategy": "anonymous"},
                "deployment": {"view_only_mode": True},
                "external_services": {"prometheus": {"url": REMOTE_PROMETHEUS_URL}},
                "istio_namespace": "istio-system",
                "server": {"port": 20001, "web_root": "/"},
            },
            does_not_raise(),
        ),
        (
            # Inactive: Missing Prometheus relation should raise an exception.
            None,
            None,
            pytest.raises(ConfigurationBlockingError),
        ),
    ],
)
def test_kiali_config(
    this_charm,
    this_charm_context,
    prometheus_url,
    expected,
    expected_context,
):
    """Test that the generated kiali configuration matches the expected output or raises the expected exception."""
    with this_charm_context(this_charm_context.on.update_status(), state=State()) as manager:
        charm: this_charm = manager.charm
        # Default value in case we raise an exception
        kiali_config = None
        with expected_context:
            kiali_config = charm._generate_kiali_config(prometheus_url=prometheus_url)
        assert kiali_config == expected
