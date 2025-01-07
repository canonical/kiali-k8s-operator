#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A Juju charm for managing Kiali."""

import logging
from pathlib import Path
from typing import List

import ops
import yaml
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceConsumer
from ops import Port, StatusBase, pebble
from ops.pebble import Layer
import requests

from config import CharmConfig

LOGGER = logging.getLogger(__name__)
SOURCE_PATH = Path(__file__).parent


KIALI_CONFIG_PATH = Path("/kiali-configuration/config.yaml")
KIALI_PORT = 20001

# TODO: Required by the GrafanaSourceConsumer library, but barely used in this charm.  Can we remove it?
PEER = "grafana"


class KialiCharm(ops.CharmBase):
    """Charm for managing Kiali."""

    def __init__(self, *args):
        super().__init__(*args)
        self._parsed_config = None

        self._container = self.unit.get_container("kiali")

        # Connection to prometheus/grafana-source integration
        self._prometheus_source = GrafanaSourceConsumer(self, "prometheus")
        self.framework.observe(
            self._prometheus_source.on.sources_changed,  # pyright: ignore
            self._reconcile,
        )
        # Not sure we need this, but kept it here for completeness.
        self.framework.observe(
            self._prometheus_source.on.sources_to_delete_changed,  # pyright: ignore
            self._reconcile,
        )

        self.framework.observe(self.on.collect_unit_status, self.on_collect_status)
        self.framework.observe(self.on.config_changed, self._reconcile)

        # Expose the Kiali workload through the service
        self.unit.set_ports(Port("tcp", KIALI_PORT))

    def on_collect_status(self, e: ops.CollectStatusEvent):
        """Collect and report the statuses of the charm."""
        # TODO: Extract the generation of these statuses to a helper method, that way other parts of the charm can know
        #  if the charm is active too.
        statuses: List[StatusBase] = []

        if not self._container.can_connect():
            statuses.append(ops.WaitingStatus("Waiting for the Kiali container to be ready"))

        if not self._is_prometheus_source_available():
            statuses.append(ops.BlockedStatus("Prometheus source is not available"))

        if not self._is_kiali_available():
            statuses.append(ops.WaitingStatus("Kiali is configured and container is ready, but Kiali is not available"))

        if len(statuses) == 0:
            statuses.append(ops.ActiveStatus())

        for status in statuses:
            e.add_status(status)

    def _reconcile(self, _event: ops.ConfigChangedEvent):
        """Reconcile the entire state of the charm."""
        self._configure_kiali_workload()

    def _configure_kiali_workload(self):
        """Configure the Kiali workload, if possible, logging errors otherwise.

        This will generate and push the Kiali configuration to the container, restarting the service if necessary.  If
        any known errors occur, they will be logged and this method will return without error.
        """
        # TODO: Can I make this generic and share with other charms?
        name = "kiali"
        if not self._container.can_connect():
            LOGGER.warning(f"Container is not ready, cannot configure {name}")
            return

        layer = self._generate_kiali_layer()
        try:
            new_config = self._generate_kiali_config()
        except GrafanaSourceError as e:
            LOGGER.warning(f"Failed to generate {name} configuration, got error: {e}")
            # TODO: actually shut down the service and remove the configuration
            # LOGGER.warning(f"Shutting down {name} service and removing existing configuration")
            return

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
        prometheus_url = self._get_prometheus_source_url()
        config = {
            "auth": {
                "strategy": "anonymous",
            },
            "external_services": {
                "prometheus": {
                    "url": prometheus_url
                }
            },
            # TODO: Use the actual istio namespace
            "istio_namespace": "istio-system",
            "server": {"port": KIALI_PORT, "web_root": "/kiali"},
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

    def _get_prometheus_source_url(self):
        """Get the Prometheus source configuration.

        Raises a SourceNotAvailableError if there are no sources or the data is not complete.
        """
        if not (prometheus_sources := self._prometheus_source.sources):
            raise GrafanaSourceError("No Prometheus sources available")
        if len(prometheus_sources) > 1:
            raise GrafanaSourceError("Multiple Prometheus sources available, expected only one")
        if not (url := prometheus_sources[0].get('url', None)):
            raise GrafanaSourceError("Prometheus source data is incomplete - url not available")
        return url

    def _is_kiali_available(self):
        """Return True if the Kiali workload is available, else False.
        """
        if not self._container.can_connect():
            LOGGER.info(f"Cannot connect to container: {e} - Kiali is not available")
            return False

        try:
            self._container.pull(KIALI_CONFIG_PATH)
        except pebble.PathError as e:
            LOGGER.info(f"Could not find Kiali configuration file: {e} - Kiali is not available")
            return False

        kiali_config = self._generate_kiali_config()
        kiali_local_url = f"http://localhost:{kiali_config["server"]["port"]}/kiali"
        if requests.get(url=kiali_local_url).status_code != 200:
            LOGGER.info(f"Kiali is not available at {kiali_local_url}")
            return False

        return True

    def _is_prometheus_source_available(self):
        """Return True if the Prometheus source is available, else False."""
        try:
            self._get_prometheus_source_url()
            return True
        except GrafanaSourceError:
            return False

    # Properties

    @property
    def parsed_config(self):
        """Return a validated and parsed configuration object."""
        if self._parsed_config is None:
            config = dict(self.model.config.items())
            self._parsed_config = CharmConfig(**config)  # pyright: ignore
        return self._parsed_config.dict(by_alias=True)

    # TODO: Required by the GrafanaSourceConsumer library, but not used in this charm.  Can we remove it?
    @property
    def peers(self):
        """Fetch the peer relation."""
        return self.model.get_relation(PEER)


    # Helpers


def _is_container_file_equal_to(container, filename, file_contents: str) -> bool:
    """Return True if the passed file_contents matches the filename inside the container, else False.

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


class GrafanaSourceError(Exception):
    """Raised when the Grafana source data is not available."""
    pass


if __name__ == "__main__":
    ops.main.main(KialiCharm)
