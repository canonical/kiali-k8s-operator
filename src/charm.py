#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A Juju charm for managing Kiali."""

import logging
from pathlib import Path
from typing import List

import ops
import yaml
from ops import pebble, StatusBase
from ops.pebble import Layer

from config import CharmConfig

LOGGER = logging.getLogger(__name__)
SOURCE_PATH = Path(__file__).parent


KIALI_CONFIG_PATH = Path("/kiali-configuration/config.yaml")

class KialiCharm(ops.CharmBase):
    """Charm for managing Kiali."""

    def __init__(self, *args):
        super().__init__(*args)
        self._parsed_config = None

        self._container = self.unit.get_container("kiali")

        self.framework.observe(self.on.collect_unit_status, self.on_collect_status)
        self.framework.observe(self.on.config_changed, self._reconcile)

    def on_collect_status(self, e: ops.CollectStatusEvent):
        """Collect and report the statuses of the charm."""
        statuses: List[StatusBase] = []

        if not self._container.can_connect():
            statuses.append(ops.WaitingStatus("Waiting for the Kiali container to be ready"))
        # TODO: if prometheus relation missing, add a BlockedStatus
        # TODO: add something that confirms the service is actually alive?  or maybe a check of the pebble checks?
        #  see https://github.com/canonical/cos-lib/blob/main/src/cosl/coordinated_workers/worker.py#L246 for ideas
        statuses.append(ops.ActiveStatus(""))

        for status in statuses:
            e.add_status(status)

    def _reconcile(self, _event: ops.ConfigChangedEvent):
        """Reconcile the entire state of the charm."""
        self._configure_kiali_workload()
        self.unit.status = ops.BlockedStatus("todo: implement reconcile")

    def _configure_kiali_workload(self):
        """Configure the Kiali workload."""
        # TODO: Can I make this generic and share with other charms?
        name = "kiali"
        layer = self._generate_kiali_layer()
        if self._container.can_connect():
            new_config = self._generate_kiali_config()
            # TODO: Ensure this works as expected
            should_restart = not _is_container_file_equal_to(self._container, KIALI_CONFIG_PATH, new_config)
            self._container.push(KIALI_CONFIG_PATH, new_config, make_dirs=True)
            self._container.add_layer(name, layer, combine=True)
            self._container.autostart()

            if should_restart:
                LOGGER.info(f"new config detected for {name}, restarting the service")
                self._container.replan()

    def _generate_kiali_config(self) -> str:
        """Generate the Kiali configuration."""
        config = {
            "auth": {
                "strategy": "anonymous",
            },
            "external_services": {
                "prometheus": {
                    # TODO: Use the actual Prometheus endpoint
                    "url": "http://prometheus.prometheus:9090"
                }
            },
            # TODO: Use the actual istio namespace
            "istio_namespace": "istio-system",
            "server": {
                "web_root": "/kiali"
            }
        }
        return yaml.dump(config)

    @staticmethod
    def _generate_kiali_layer() -> Layer:
        """Generate the Kiali layer."""
        # TODO: Add pebble checks?
        return Layer(
            {
                "summary": "Kiali",
                "description": "The Kiali dashboard for Istio",
                "services": {
                    "kiali": {
                        "override": "replace",
                        "summary": "kiali",
                        "command": f"/opt/kiali/kiali -config {KIALI_CONFIG_PATH}",
                        "startup": "enabled",
                    }
                }
            }
        )

    # Properties

    @property
    def parsed_config(self):
        """Return a validated and parsed configuration object."""
        if self._parsed_config is None:
            config = dict(self.model.config.items())
            self._parsed_config = CharmConfig(**config)  # pyright: ignore
        return self._parsed_config.dict(by_alias=True)

    # Helpers

def _is_container_file_equal_to(container, filename, file_contents: str) -> bool:
    """Returns True if the passed file_contents matches the filename inside the container, else False.

    Returns False if the container is not accessible, the file does not exist, or the contents do not match.
    """
    if not container.can_connect():
        return False

    try:
        current_contents = container.pull(filename).read()
    except (pebble.ProtocolError, pebble.PathError) as e:
        LOGGER.warning(f"Could not check {filename} - got error while retrieving the file: {e}")
        return False

    return current_contents == file_contents


if __name__ == "__main__":
    ops.main.main(KialiCharm)
