#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from contextlib import nullcontext
from unittest.mock import patch

import pytest
from observability_charm_tools.exceptions import WaitingStatusError

from charm import _is_kiali_available


@pytest.mark.parametrize(
    "status_code, expected_result, context_raised",
    [
        (200, True, nullcontext()),
        (404, None, pytest.raises(WaitingStatusError)),
        (500, None, pytest.raises(WaitingStatusError)),
    ],
)
def test_is_kiali_available(mock_requests_get, status_code, expected_result, context_raised):
    """Tests that _is_kiali_available returns the expected result."""
    with patch("charm.requests.get") as mock_requests_get:
        with context_raised:
            mock_requests_get.return_value.status_code = status_code
            assert _is_kiali_available("http://kiali") == expected_result
