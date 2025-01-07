# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

import pytest
from helpers import build_charm

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def charm_under_test():
    return build_charm(charm_root_path="./", from_environment_variable="KIALI_CHARM")
