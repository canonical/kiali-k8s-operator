#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
resources = {
    "kiali-image": METADATA["resources"]["kiali-image"]["upstream-source"],
}


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, this_charm):
    """Build the charm-under-test and deploy it."""
    # Deploy the charm and wait for active/idle status
    await asyncio.gather(
        ops_test.model.deploy(
            this_charm, resources=resources, application_name=APP_NAME, trust=True
        ),
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
    )


@pytest.mark.abort_on_fail
async def test_kiali_is_available(ops_test: OpsTest):
    """Assert that Kiali is up and available inside the cluster."""
    assert False, "Test not implemented"
