#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A Juju charm for managing Kiali."""

import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import ops
import requests
import yaml
from charms.istio_beacon_k8s.v0.service_mesh import ServiceMeshConsumer
from charms.istio_k8s.v0.istio_metadata import IstioMetadataRequirer
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.mimir_coordinator_k8s.v0.prometheus_api import PrometheusApiRequirer
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from observability_charm_tools.exceptions import BlockedStatusError, WaitingStatusError
from observability_charm_tools.status_handling import StatusManager
from ops import Container, Port, pebble
from ops.pebble import Layer

from charm_config import CharmConfig
from workload_config import (
    AuthConfig,
    DeploymentConfig,
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
ISTIO_RELATION = "istio-metadata"
PROMETHEUS_RELATION = "prometheus"


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
        self._prometheus_source = PrometheusApiRequirer(self.model.relations, PROMETHEUS_RELATION)
        self.framework.observe(self.on[PROMETHEUS_RELATION].relation_changed, self.reconcile)
        self.framework.observe(self.on[PROMETHEUS_RELATION].relation_broken, self.reconcile)

        # Connection to the service mesh
        self._mesh = ServiceMeshConsumer(self)

        # Connection to istio-k8s
        self._istio_metadata = IstioMetadataRequirer(self.model.relations, ISTIO_RELATION)
        self.framework.observe(self.on[ISTIO_RELATION].relation_changed, self.reconcile)
        self.framework.observe(self.on[ISTIO_RELATION].relation_broken, self.reconcile)

        self.framework.observe(self.on.kiali_pebble_ready, self.reconcile)
        self.framework.observe(self.on.config_changed, self.reconcile)

        # Expose the Kiali workload through the service
        self.unit.set_ports(Port("tcp", KIALI_PORT))

    def reconcile(self, _event: ops.ConfigChangedEvent):
        """Reconcile the entire state of the charm."""
        status_manager = StatusManager()

        # Set a default value for any returns in the below context in case of an error
        prometheus_url = None
        with status_manager:
            prometheus_url = self._get_prometheus_source_url()

        istio_namespace = None
        with status_manager:
            istio_namespace = self._get_istio_namespace()

        kiali_config = None
        with status_manager:
            kiali_config = self._generate_kiali_config(
                prometheus_url=prometheus_url, istio_namespace=istio_namespace
            )

        with status_manager:
            self._configure_kiali_workload(kiali_config)

        with status_manager:
            _is_kiali_available(self._internal_url + self._prefix)

        # TODO: Log all statuses

        # Set the unit to be the worst status
        self.unit.status = status_manager.worst()

    def _configure_kiali_workload(self, new_config):
        """Configure the Kiali workload, if possible, logging errors otherwise.

        This will generate and push the Kiali configuration to the container, restarting the service if necessary.
        The purpose here is that this should always attempt to configure/start Kiali, but it does not guarantee Kiali is
        running after completion.  If any known errors occur, they will be logged and this method will return without
        error.  To confirm if Kiali is working, check the status of the Kiali workload directly.

        Args:
            new_config: The new configuration to push to the Kiali workload.
        """
        name = "kiali"
        if not self._container.can_connect():
            LOGGER.debug(f"Container is not ready, cannot configure {name}")
            raise WaitingStatusError("Container is not ready, cannot configure Kiali")

        if not new_config:
            raise BlockedStatusError("No configuration available for Kiali")

        layer = self._generate_kiali_layer()
        new_config = yaml.dump(new_config)

        should_restart = not _is_container_file_equal_to(
            self._container, str(KIALI_CONFIG_PATH), new_config
        )
        self._container.push(KIALI_CONFIG_PATH, new_config, make_dirs=True)
        self._container.add_layer(name, layer, combine=True)
        self._container.autostart()

        if should_restart:
            LOGGER.info(f"new config detected for {name}, restarting the service")
            self._container.restart(KIALI_PEBBLE_SERVICE_NAME)

    def _generate_kiali_config(
        self, prometheus_url: Optional[str], istio_namespace: Optional[str]
    ) -> dict:
        """Generate the Kiali configuration."""
        if not prometheus_url:
            raise BlockedStatusError("Cannot configure Kiali - no Prometheus url available")

        if not istio_namespace:
            raise BlockedStatusError("Cannot configure Kiali - no related istio available")

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
            deployment=DeploymentConfig(view_only_mode=self.parsed_config["view-only-mode"]),
            external_services=external_services,
            istio_namespace=istio_namespace,
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

    def _get_istio_namespace(self) -> str:
        """Get the istio namespace configuration.

        Raises:
            BlockedStatusError: If no istio relation is available
            WaitingStatusError: If the istio relation is available, but the data is incomplete
        """
        if len(self._istio_metadata.relations) == 0:
            raise BlockedStatusError("Missing required relation to istio provider")
        if not (istio_data := self._istio_metadata.get_data()):
            raise WaitingStatusError("Istio relation established, but data is missing or invalid")
        return istio_data.root_namespace

    def _get_prometheus_source_url(self) -> str:
        """Get the Prometheus source configuration.

        Returns, in this order, the first of:
        * prometheus's ingress_url
        * prometheus's direct_url

        Raises:
            BlockedStatusError: If no Prometheus sources are available
            WaitingStatusError: If Prometheus sources are available, but the data is incomplete
        """
        if len(self._prometheus_source.relations) == 0:
            raise BlockedStatusError("Missing required relation to prometheus provider")
        if not (prometheus_data := self._prometheus_source.get_data()):
            raise WaitingStatusError(
                "Prometheus relation established, but data is missing or invalid"
            )
        # Return ingress_url if not None, else direct_url
        return str(prometheus_data.ingress_url or prometheus_data.direct_url)

    def _is_prometheus_source_available(self):
        """Return True if Prometheus is available, else False."""
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
        return self._parsed_config.model_dump(by_alias=True)

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
            msg = (
                f"Kiali is not available at {kiali_url}- see other logs/statuses for reasons why."
                f"  If no other errors exist, this may be transient as the service starts."
            )
            raise WaitingStatusError(msg)
    except requests.exceptions.ConnectionError as e:
        msg = (
            f"Kiali is not available at {kiali_url} - got connection error: {e}."
            f"  If no other errors exist, this may be transient as the service starts."
        )
        raise WaitingStatusError(msg)
    return True


class PrometheusSourceError(Exception):
    """Raised when the Prometheus data is not available."""

    pass


if __name__ == "__main__":
    ops.main.main(KialiCharm)
