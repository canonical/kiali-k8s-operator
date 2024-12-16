#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from scenario import State


def test_start_charm_active(this_charm_context):
    state = State()
    out = this_charm_context.run(this_charm_context.on.config_changed(), state)
    assert out.unit_status.name == "active"
