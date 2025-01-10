#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_requests_get(request):
    """Mock requests.get to return a 200 status code so it looks like Kiali is available in the collect_status hook.

    To disable this fixture in a specific test, mark it with @pytest.marl.disable_requests_get_autouse.
    """
    if "disable_requests_get_autouse" in request.keywords:
        yield
    else:
        with patch("charm.requests.get") as mock_requests_get:
            mock_requests_get.return_value.status_code = 200
            yield mock_requests_get
