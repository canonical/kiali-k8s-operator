#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import json

import pytest
from ops import ActiveStatus, BlockedStatus, WaitingStatus
from scenario import Container, PeerRelation, Relation, State

from charm import KIALI_CONFIG_PATH

REMOTE_PROMETHEUS_APP_NAME = "grafana"
REMOTE_PROMETHEUS_MODEL = "some-model"
REMOTE_PROMETHEUS_MODEL_UUID = "1"
REMOTE_PROMETHEUS_TYPE = "prometheus"
REMOTE_PROMETHEUS_URL = "http://prometheus:9090"


def test_charm_processes_prometheus_relation_data(this_charm_context):
    """Tests that, when supplied with a prometheus relation (grafana-source interface), the charm processes the data.

    This test is important because other tests in this suite rely on a mock of the GrafanaSourceConsumer peer relation,
    so they may pass even if GrafanSourceConsumer changes.
    """
    # Arrange
    container = Container(
        name="kiali",
        can_connect=True,
    )
    prometheus_relation = mock_prometheus_relation()
    grafana_source_peer_relation = PeerRelation("grafana")

    state = State(
        containers=[container],
        relations=[prometheus_relation, grafana_source_peer_relation],
        leader=True,
    )

    mock_peer_relation = mock_grafana_consumer_peer_relation()

    # Act
    state_with_grafana_source_processed = this_charm_context.run(this_charm_context.on.relation_changed(prometheus_relation), state)

    # Assert that the peer relation data is as expected.
    # If this test fails due to GrafanaSourceConsumer changing its peer relation data format, update the
    # mock_grafana_consumer_peer_relation method to match the new format as other tests in this suite use the mock
    # rather than the real data.
    assert compare_sources_ignoring_relation_id(mock_peer_relation.local_app_data["sources"], state_with_grafana_source_processed.get_relation(grafana_source_peer_relation.id).local_app_data["sources"]), "This failing indicates GrafanaSourceConsumer may have changed its peer data format.  See comments for this test for additional instructions."


def mock_prometheus_relation() -> Relation:
    """Return a mock relation to prometheus."""
    return Relation(
        endpoint="prometheus",
        interface="grafana_datasource",
        remote_app_name=REMOTE_PROMETHEUS_APP_NAME,
        remote_app_data = {
            "grafana_source_data": json.dumps({
                "model": REMOTE_PROMETHEUS_MODEL,
                "model_uuid": REMOTE_PROMETHEUS_MODEL_UUID,
                "application": REMOTE_PROMETHEUS_APP_NAME,
                "type": REMOTE_PROMETHEUS_TYPE,
                # "extra_fields": {},
                # "secure_extra_fields": None,
            }),
        },
        remote_units_data = {
            0: {"grafana_source_host": REMOTE_PROMETHEUS_URL},
        }
    )


def mock_grafana_consumer_peer_relation() -> PeerRelation:
    """Return a mock PeerRelation that is generated by GrafanaSourceConsumer for the mock_prometheus_relation."""
    return PeerRelation(
        "grafana",
        local_app_data={
            "sources": json.dumps({
                "1": [{
                    "unit": f"{REMOTE_PROMETHEUS_APP_NAME}/0",
                    "source_name": f"juju_{REMOTE_PROMETHEUS_MODEL}_{REMOTE_PROMETHEUS_MODEL_UUID}_{REMOTE_PROMETHEUS_APP_NAME}_0",
                    "source_type": REMOTE_PROMETHEUS_TYPE,
                    "url": REMOTE_PROMETHEUS_URL
                }]
            }),
            "sources_to_delete": json.dumps([]),
        },
    )


def compare_sources_ignoring_relation_id(sources1: str, sources2: str):
    """Compare two Grafana Source peer relation sources json strings, ignoring the relation_id key.

    This comparison is done ignoring the relation_id key because it depends on the test suite, not the object under
    test.

    Essentially tests if source1.values() == source2.values().
    """
    sources1 = json.loads(sources1)
    sources2 = json.loads(sources2)

    return list(sources1.values()) == list(sources2.values())


@pytest.mark.parametrize(
    "container, relations, expected_status, config_file_exists",
    [
        (
            # Has all inputs - Active
            Container(name="kiali", can_connect=True),
            [
                mock_prometheus_relation(),
                mock_grafana_consumer_peer_relation(),
            ],
            ActiveStatus,
            True
        ),
        (
            # Inactive - container not ready
            Container(name="kiali", can_connect=False),
            [
                mock_prometheus_relation(),
                mock_grafana_consumer_peer_relation(),
            ],
            WaitingStatus,
            False
        ),
        (
            # Inactive - prometheus relation not ready
            Container(name="kiali", can_connect=True),
            [],
            BlockedStatus,
            False
        ),
    ]
)
def test_charm_given_inputs(this_charm_context, container, relations, expected_status, config_file_exists):
    """Tests that the charm responds as expected to standard inputs."""
    # Arrange
    state = State(
        containers=[container],
        relations=relations,
        leader=True,
    )

    # Act - trigger the config-changed event, which should be enough to reconcile the charm to Active
    out = this_charm_context.run(this_charm_context.on.config_changed(), state)

    # Assert
    # that charm is active
    assert isinstance(out.unit_status, expected_status)

    # that a config file was generated
    assert (container.get_filesystem(this_charm_context) / KIALI_CONFIG_PATH.relative_to("/")).exists() == config_file_exists
