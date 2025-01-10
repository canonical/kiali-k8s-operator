#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest

from charm import _is_kiali_available


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
