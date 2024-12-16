# Copyright 2024 Canonical Ltd
# See LICENSE file for licensing details.

import ops
import ops.testing
import pytest
from ops.model import ActiveStatus

from charm import IstioCoreCharm


@pytest.fixture()
def harness():
    harness = ops.testing.Harness(IstioCoreCharm)
    harness.set_model_name("istio-system")
    yield harness
    harness.cleanup()


class TestCharm:
    def test_charm_begins_active(
        self,
        harness,
    ):
        harness.begin_with_initial_hooks()

        assert isinstance(harness.charm.unit.status, ActiveStatus)

    def test_charm_config_parsing(self, harness):
        """Assert that the default configuration can be validated and is accessible."""
        harness.begin()

        # parsed_config = harness.charm.parsed_config
        # Assert an example config is as expected
        assert False, "Test not implemented"
