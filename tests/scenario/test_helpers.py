#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest

from ops import CharmBase
from scenario import Container, Context, Mount, State

from charm import _is_container_file_equal_to, _is_kiali_available

CONTAINER_NAME = "container"


class DummyCharm(CharmBase):
    META = {"name": "dummy", "containers": {CONTAINER_NAME: {}}}

    def __init__(self, framework):
        super().__init__(framework)
        self.container = self.unit.containers[CONTAINER_NAME]


def test_is_container_file_equal_to(tmp_path):
    """Tests that _is_container_file_equal_to correctly can check if a container's file equals given data."""
    # Arrange a scenario context with a container that has a file mount
    ctx = Context(charm_type=DummyCharm, meta=DummyCharm.META)
    container_storage = tmp_path / "container"
    filename = "/dummy/test.txt"

    scenario_container = Container(
        name=CONTAINER_NAME,
        can_connect=True,
        mounts={"dummy": Mount(location=filename, source=container_storage)},
    )

    # Act/Assert
    # Execute this in a dummy charm so scenario can populate an ops-style container for us
    with ctx(ctx.on.update_status(), State(containers=[scenario_container])) as manager:
        charm = manager.charm
        model_container = charm.container
        sample_file_data = "test"

        # Assert that, before we add a file, the contents will not match
        assert _is_container_file_equal_to(model_container, filename, sample_file_data) is False

        # Assert that, after we've added a file, the contents will match
        model_container.push(filename, sample_file_data, make_dirs=True)
        assert _is_container_file_equal_to(model_container, filename, sample_file_data) is True

        # Assert that comparing the above file to arbitrarily different data will not match
        assert (
            _is_container_file_equal_to(model_container, filename, sample_file_data + "NOT")
            is False
        )


@pytest.mark.parametrize(
    "status_code, expected_result",
    [
        (200, True),
        (404, False),
        (500, False),
    ],
)
def test_is_kiali_available(mock_requests_get, status_code, expected_result):
    """Tests that _is_kiali_available returns the expected result."""
    with patch("charm.requests.get") as mock_requests_get:
        mock_requests_get.return_value.status_code = status_code
        assert _is_kiali_available("http://kiali") == expected_result
