#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A Juju charm for managing Kiali."""

import logging
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import ops
import requests
import yaml
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceConsumer
from charms.istio_beacon_k8s.v0.service_mesh import ServiceMeshConsumer
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from ops import Container, Port, StatusBase, pebble
from ops.pebble import Layer

from charm_config import CharmConfig
from workload_config import (
    AuthConfig,
    ExternalServicesConfig,
    KialiConfigSpec,
    PrometheusConfig,
    ServerConfig,
)

LOGGER = logging.getLogger(__name__)
SOURCE_PATH = Path(__file__).parent


KIALI_CONFIG_PATH = Path("/kiali-configuration/config.yaml")
KIALI_PORT = 20001
KIALI_PEBBLE_SERVICE_NAME = "kiali"

# TODO: Required by the GrafanaSourceConsumer library, but barely used in this charm.  Can we remove it?
PEER = "grafana"


class KialiCharm(ops.CharmBase):
    """Charm for managing Kiali."""

    def __init__(self, *args):
        super().__init__(*args)
        self._parsed_config = None

        self._container = self.unit.get_container("kiali")

        # O11y Integration
        self._scraping = MetricsEndpointProvider(
            self,
            jobs=[{"static_configs": [{"targets": ["*:9090"]}]}],
        )
        self._logging = LogForwarder(self)

        # Ingress Integration
        self._ingress = IngressPerAppRequirer(
            charm=self,
            port=KIALI_PORT,
            strip_prefix=False,
            redirect_https=True,
            scheme="http",
        )
        self.framework.observe(self._ingress.on.ready, self.reconcile)
        self.framework.observe(self._ingress.on.revoked, self.reconcile)

        # Connection to prometheus/grafana-source integration
        self._prometheus_source = GrafanaSourceConsumer(self, "prometheus")
        self.framework.observe(
            self._prometheus_source.on.sources_changed,  # pyright: ignore
            self.reconcile,
        )
        # Not sure we need this, but kept it here for completeness.
        self.framework.observe(
            self._prometheus_source.on.sources_to_delete_changed,  # pyright: ignore
            self.reconcile,
        )

        # Connection to the service mesh
        self._mesh = ServiceMeshConsumer(self)

        self.framework.observe(self.on.collect_unit_status, self.on_collect_status)
        self.framework.observe(self.on.config_changed, self.reconcile)

        # Expose the Kiali workload through the service
        self.unit.set_ports(Port("tcp", KIALI_PORT))

    def on_collect_status(self, e: ops.CollectStatusEvent):
        """Collect and report the statuses of the charm."""
        # TODO: Extract the generation of these statuses to a helper method, that way other parts of the charm can know
        #  if the charm is active too.
        statuses: List[StatusBase] = []

        if not self._container.can_connect():
            statuses.append(ops.WaitingStatus("Waiting for the Kiali container to be ready"))

        if not (prometheus_source_available := self._is_prometheus_source_available()):
            statuses.append(ops.BlockedStatus("Prometheus source is not available"))

        if prometheus_source_available:
            # Only valid if we have a prometheus source
            if not _is_kiali_available(self._internal_url + self._prefix):
                statuses.append(
                    ops.WaitingStatus(
                        "Kiali is configured and container is ready, but Kiali's web server is not available"
                    )
                )

        if len(statuses) == 0:
            statuses.append(ops.ActiveStatus())

        for status in statuses:
            e.add_status(status)

    def reconcile(self, _event: ops.ConfigChangedEvent):
        """Reconcile the entire state of the charm."""
        self._configure_kiali_workload()

    def _configure_kiali_workload(self):
        """Configure the Kiali workload, if possible, logging errors otherwise.

        This will generate and push the Kiali configuration to the container, restarting the service if necessary.
        The purpose here is that this should always attempt to configure/start Kiali, but it does not guarantee Kiali is
        running after completion.  If any known errors occur, they will be logged and this method will return without
        error.  To confirm if Kiali is working, check the status of the Kiali workload directly.
        """
        # TODO: Can we make this generic and share with other charms?
        name = "kiali"
        if not self._container.can_connect():
            LOGGER.debug(f"Container is not ready, cannot configure {name}")
            return

        layer = self._generate_kiali_layer()
        try:
            new_config = yaml.dump(self._generate_kiali_config())
        except PrometheusSourceError as e:
            LOGGER.warning(f"Failed to generate {name} configuration, got error: {e}")
            # TODO: actually shut down the service and remove the configuration
            # LOGGER.warning(f"Shutting down {name} service and removing existing configuration")
            return

        should_restart = not _is_container_file_equal_to(
            self._container, str(KIALI_CONFIG_PATH), new_config
        )
        self._container.push(KIALI_CONFIG_PATH, new_config, make_dirs=True)
        self._container.add_layer(name, layer, combine=True)
        self._container.autostart()

        if should_restart:
            LOGGER.info(f"new config detected for {name}, restarting the service")
            self._container.restart(KIALI_PEBBLE_SERVICE_NAME)

    def _generate_kiali_config(self) -> dict:
        """Generate the Kiali configuration."""
        prometheus_url = self._get_prometheus_source_url()
        external_services = ExternalServicesConfig(prometheus=PrometheusConfig(url=prometheus_url))

        # TODO: implement _get_tempo_source_url()
        # tempo_url = self._get_tempo_source_url()
        # if tempo_url:
        #     external_services.tracing = TracingConfig(
        #         enabled=True,
        #         internal_url=tempo_url,
        #         use_grpc=True,
        #         external_url=tempo_url,
        #         grpc_port=9096,
        # TODO: work on below functionality when we figure out how to get tempo's grafana source uid
        # tempo_config=TracingTempoConfig(
        #     org_id="1",
        #     datasource_uid=self._get_tempo_source_uid(),
        #     url_format="grafana",
        # ),
        # )

        # TODO:implement _get_grafana_source_url()
        # grafana_url = self._get_grafana_source_url()
        # if grafana_url:
        # external_services.grafana = GrafanaConfig(
        #     enabled=True,
        #     external_url=grafana_url,
        # )

        kiali_config = KialiConfigSpec(
            auth=AuthConfig(strategy="anonymous"),
            external_services=external_services,
            # TODO: Use the actual istio namespace (https://github.com/canonical/kiali-k8s-operator/issues/4)
            istio_namespace="istio-system",
            server=ServerConfig(port=KIALI_PORT, web_root=self._prefix),
        )
        return kiali_config.model_dump(exclude_none=True)

    @staticmethod
    def _generate_kiali_layer() -> Layer:
        """Generate the Kiali layer."""
        # TODO: Add pebble checks?
        return Layer(
            {
                "summary": "Kiali",
                "description": "The Kiali dashboard for Istio",
                "services": {
                    KIALI_PEBBLE_SERVICE_NAME: {
                        "override": "replace",
                        "summary": "kiali",
                        "command": f"/opt/kiali/kiali -config {KIALI_CONFIG_PATH}",
                        "startup": "enabled",
                        "working-dir": "/opt/kiali",
                    }
                },
            }
        )

    def _get_prometheus_source_url(self):
        """Get the Prometheus source configuration.

        Raises a SourceNotAvailableError if there are no sources or the data is not complete.
        """
        if not (prometheus_sources := self._prometheus_source.sources):
            raise PrometheusSourceError("No Prometheus sources available")
        if len(prometheus_sources) > 1:
            raise PrometheusSourceError("Multiple Prometheus sources available, expected only one")
        if not (url := prometheus_sources[0].get("url", None)):
            raise PrometheusSourceError("Prometheus source data is incomplete - url not available")
        return url

    def _is_prometheus_source_available(self):
        """Return True if the Prometheus source is available, else False."""
        try:
            self._get_prometheus_source_url()
            return True
        except PrometheusSourceError:
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

    @property
    def _prefix(self) -> str:
        """Return the prefix extracted from the external URL or '/' if the URL is None."""
        if self._ingress.url:
            return urlparse(self._ingress.url).path
        return "/"

    @property
    def _internal_url(self) -> str:
        """Return the fqdn dns-based in-cluster (private) address of kiali."""
        return f"http://localhost:{KIALI_PORT}"


# Helpers
def _is_container_file_equal_to(container: Container, filename: str, file_contents: str) -> bool:
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


def _is_kiali_available(kiali_url):
    """Return True if the Kiali workload is available, else False."""
    # TODO: This feels like a pebble check.  We should move this to a pebble check, then just confirm pebble checks are
    #  passing
    try:
        if requests.get(url=kiali_url).status_code != 200:
            LOGGER.info(f"Kiali is not available at {kiali_url}")
            return False
    except requests.exceptions.ConnectionError as e:
        LOGGER.info(f"Kiali is not available at {kiali_url} - got error: {e}")
        return False

    return True


class PrometheusSourceError(Exception):
    """Raised when the Prometheus data is not available."""

    pass


if __name__ == "__main__":
    ops.main.main(KialiCharm)
