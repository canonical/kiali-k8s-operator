#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from scenario import State, Container

from charm import KIALI_CONFIG_PATH


def test_charm_active_when_has_all_inputs(this_charm_context):
    """Tests that, when supplied with all required inputs, the charm is active and workload configured."""
    # Arrange
    container = Container(
        name="kiali",
        can_connect=True,
    )
    state = State(
        containers=[container]
    )

    # Act
    out = this_charm_context.run(this_charm_context.on.config_changed(), state)

    # Assert
    # that charm is active
    assert out.unit_status.name == "active"

    # that a config file was generated
    assert (container.get_filesystem(this_charm_context) / KIALI_CONFIG_PATH.relative_to("/")).exists()
