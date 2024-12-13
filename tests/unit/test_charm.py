# Copyright 2024 Canonical Ltd
# See LICENSE file for licensing details.

import ops
import ops.testing
import pytest
from ops.model import ActiveStatus

from charm import KialiCharm


@pytest.fixture()
def harness():
    harness = ops.testing.Harness(KialiCharm)
    harness.set_model_name("istio-system")
    yield harness
    harness.cleanup()


class TestCharm:
    def test_that_updated_kiali_config_triggers_replan(self, harness):
        assert False, "Test not implemented"

    def test_charm_config_parsing(self, harness):
        """Assert that the default configuration can be validated and is accessible."""
        harness.begin()

        # parsed_config = harness.charm.parsed_config
        # Assert an example config is as expected
        assert False, "Test not implemented"

    # TODO: Can we test the on_collect_status method here somehow?

class TestCharmHelpers:
    def test_generate_kiali_config(self):
        assert False, "Test not implemented"

    def test_is_container_file_equal_to(self):
        assert False, "Test not implemented"