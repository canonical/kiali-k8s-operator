#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A Juju charm for managing Kiali."""

import logging
from pathlib import Path

import ops

from config import CharmConfig

LOGGER = logging.getLogger(__name__)

SOURCE_PATH = Path(__file__).parent

class KialiCharm(ops.CharmBase):
    """Charm for managing Kiali."""

    def __init__(self, *args):
        super().__init__(*args)
        self._parsed_config = None
        self._lightkube_field_manager: str = self.app.name

        self.framework.observe(self.on.config_changed, self._reconcile)

    def _reconcile(self, _event: ops.ConfigChangedEvent):
        """Reconcile the entire state of the charm."""
        self.unit.status = ops.BlockedStatus("todo: implement reconcile")

    # Properties

    @property
    def parsed_config(self):
        """Return a validated and parsed configuration object."""
        if self._parsed_config is None:
            config = dict(self.model.config.items())
            self._parsed_config = CharmConfig(**config)  # pyright: ignore
        return self._parsed_config.dict(by_alias=True)

    # Helpers


if __name__ == "__main__":
    ops.main.main(KialiCharm)
