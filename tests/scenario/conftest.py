#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from scenario import Context

from src.charm import KialiCharm as ThisCharm


@pytest.fixture()
def this_charm():
    yield ThisCharm


@pytest.fixture()
def this_charm_context(this_charm):
    yield Context(charm_type=this_charm)
