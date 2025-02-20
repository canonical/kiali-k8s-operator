#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A Juju charm for managing Kiali."""

import logging
from pathlib import Path
from typing import List, TypedDict, Optional
from urllib.parse import urlparse

import ops
import requests
import yaml
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceConsumer
from charms.istio_beacon_k8s.v0.service_mesh import ServiceMeshConsumer
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from cosl.interfaces.datasource_exchange import DatasourceExchange, GrafanaSourceAppData
from ops import Container, Port, StatusBase, pebble
from ops.pebble import Layer

from charm_config import CharmConfig
from workload_config import (
    AuthConfig,
    ExternalServicesConfig,
    KialiConfigSpec,
    PrometheusConfig,
    ServerConfig,
    TracingConfig,
    TracingTempoConfig,
    GrafanaConfig,
)

LOGGER = logging.getLogger(__name__)
SOURCE_PATH = Path(__file__).parent


KIALI_CONFIG_PATH = Path("/kiali-configuration/config.yaml")
KIALI_PORT = 20001
KIALI_PEBBLE_SERVICE_NAME = "kiali"

# TODO: Required by the GrafanaSourceConsumer library, but barely used in this charm.  Can we remove it?
PEER = "grafana"


TEMPO_DATASOURCE_EXCHANGE_RELAION_NAME = "tempo-datasource-exchange"


class TempoConfigurationData(TypedDict):
    """The configuration data for the Tempo datasource in Kiali."""

    datasource_uid: str
    external_url: str
    grpc_port: int
    internal_url: str


class UrlConfiguration(TypedDict):
    """The URLs of an application."""

    internal: str
    external: str


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

        # TODO: Once this exists in grafana
        self._grafana_metadata = GrafanaMetadataRequirer(...)

        # TODO: Once this exists in Tempo
        self._tempo_metadata = TempoMetadataRequirer(...)
        self._tempo_datasource_exchange = DatasourceExchange(
            charm=self,
            requirer_endpoint=TEMPO_DATASOURCE_EXCHANGE_RELAION_NAME
        )
        self.framework.observe(self.on[TEMPO_DATASOURCE_EXCHANGE_RELAION_NAME].relation_changed, self.reconcile)

    def reconcile(self, _event: ops.ConfigChangedEvent):
        """Reconcile the entire state of the charm."""
        statuses: List[StatusBase] = []

        try:
            prometheus_url = self._get_prometheus_source_url()
        except ConfigurationBlockingError as e:
            statuses.append(ops.BlockedStatus(str(e)))
            prometheus_url = None
        except ConfigurationWaitingError as e:
            statuses.append(ops.WaitingStatus(str(e)))
            prometheus_url = None

        try:
            grafana_metadata = self._get_grafana_configuration()
        except ConfigurationBlockingError as e:
            statuses.append(ops.BlockedStatus(str(e)))
            grafana_metadata = None
        except ConfigurationWaitingError as e:
            statuses.append(ops.WaitingStatus(str(e)))
            grafana_metadata = None

        try:
            tempo_configuration = self._get_tempo_configuration()
        except ConfigurationBlockingError as e:
            statuses.append(ops.BlockedStatus(str(e)))
            tempo_configuration = None
        except ConfigurationWaitingError as e:
            statuses.append(ops.WaitingStatus(str(e)))
            tempo_configuration = None

        # prometheus and grafana config are required, but tempo is optional
        try:
            kiali_config = self._generate_kiali_config(prometheus_url=prometheus_url, grafana_metadata=grafana_metadata, tempo_configuration_data=tempo_configuration)
        except ConfigurationError as e:
            statuses.append(ops.BlockedStatus(str(e)))
            kiali_config = None

        try:
            self._configure_kiali_workload(kiali_config)
        except ConfigurationError as e:
            statuses.append(ops.BlockedStatus(str(e)))

        if not _is_kiali_available(self._internal_url + self._prefix):
            statuses.append(
                ops.WaitingStatus(
                    "Kiali workload is not available.  See other logs/statuses for reasons why.  If no other statuses"
                    " are available, this may be transient."
                )
            )

        if len(statuses) == 0:
            statuses.append(ops.ActiveStatus())

        for status in statuses:
            # TODO: Log these statuses
            pass

        self.unit.status = get_worst_status(statuses)

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
            raise ConfigurationWaitingError("Container is not ready, cannot configure Kiali")

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

    def _generate_kiali_config(self, prometheus_url: str,  grafana_metadata: GrafanaMetadataAppData, tempo_configuration_data: Optional[TempoConfigurationData] = None, ) -> dict:
        """Generate the Kiali configuration."""
        if not prometheus_url:
            raise ConfigurationBlockingError("Cannot configure Kiali - no Prometheus url available")
        if not grafana_metadata:
            raise ConfigurationBlockingError("Cannot configure Kiali - no Grafana metadata available")

        external_services = ExternalServicesConfig(prometheus=PrometheusConfig(url=prometheus_url))

        if tempo_configuration_data:
            external_services.tracing = TracingConfig(
                enabled=True,
                internal_url=tempo_configuration_data['internal_url'],
                external_url=tempo_configuration_data['external_url'],
                use_grpc=True,
                grpc_port=tempo_configuration_data['grpc_port'],
                tempo_config=TracingTempoConfig(
                    org_id="1",
                    datasource_uid=tempo_configuration_data['datasource_uid'],
                    url_format="grafana",
                ),
            )

        if grafana_metadata:
            external_services.grafana = GrafanaConfig(
                enabled=True,
                external_url=grafana_metadata.url,
            )

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

    def _get_grafana_configuration(self) -> GrafanaMetadataAppData:
        """Return the Grafana configuration.

        Raises:
          ConfigurationBlockingError: If no grafana is related to this application
          ConfigurationWaitingError: If a grafana is related to this application, but its data is incomplete.
        """
        if len(self._grafana_metadata) == 0:
            raise ConfigurationBlockingError("No grafana available over the grafana-metadata relation")

        grafana_metadata = self._grafana_metadata.get_data()
        if not grafana_metadata:
            raise ConfigurationWaitingError("Waiting on related grafana application's metadata")

        return grafana_metadata

    def _get_prometheus_source_url(self) -> str:
        """Get the Prometheus source configuration.

        Raises:
            ConfigurationBlockingError: If no Prometheus sources are available
            ConfigurationWaitingError: If Prometheus sources are available, but the data is incomplete
        """
        # TODO: This currently uses the grafana-source relation, but will be refactored to use prometheus-api soon.
        #       When that refactor occurs, error handling here can be cleaned up.
        if not (prometheus_sources := self._prometheus_source.sources):
            raise ConfigurationBlockingError("No Prometheus sources available")
        # TODO: Replace this with limit=1
        if len(prometheus_sources) > 1:
            # TODO: This error is mislabeled, but will be replaced when we switch to limiting this to 1 anyway
            raise ConfigurationBlockingError("Multiple Prometheus sources available, expected only one")
        if not (url := prometheus_sources[0].get("url", None)):
            raise ConfigurationWaitingError("Prometheus source data is incomplete - url not available")
        return url

    def _get_tempo_configuration(self) -> Optional[TempoConfigurationData]:
        """Return configuration data for the related Tempo.

        Returns None if we are not related to a tempo.

        Raises:
          ConfigurationBlockingError: If a required relation for configuring Tempo is missing.
          ConfigurationWaitingError: If a Tempo is related to this application, but its data is incomplete.
        """
        try:
            tempo_metadata = self._get_tempo_metadata()
        except ConfigurationWaitingError as e:
            raise ConfigurationWaitingError(f"Tempo metadata is incomplete.  Got error: {e}")

        if not tempo_metadata:
            # We have no tempo related to us
            return None

        try:
            tempo_datasource_uid = self._get_tempo_datasource_uid()
        except ConfigurationBlockingError as e:
            raise ConfigurationBlockingError(f"Tempo datasource data is missing.  Got error: {e}")
        except ConfigurationWaitingError as e:
            raise ConfigurationWaitingError(f"Tempo datasource data is incomplete, possibly because are waiting on "
                                              f"the related application.  Got error: {e}")

        return TempoConfigurationData(
            internal_url=tempo_metadata.internal_url,
            external_url=tempo_metadata.ingress_url,
            grpc_port=tempo_metadata.grpc_port,
            datasource_uid=tempo_datasource_uid,
        )

    def _get_tempo_metadata(self):
        """Get the Tempo source urls (internal and external).

        Raises:
            ConfigurationWaitingError: If a Tempo is related to this application, but its data is incomplete.
        """
        # TODO: Use the real data type here
        if len(self._tempo_metadata) == 0:
            return None

        tempo_metadata = self._tempo_metadata.get_data()
        if not tempo_metadata:
            raise ConfigurationWaitingError("Waiting on related tempo application's metadata")

        return tempo_metadata

    def _get_tempo_datasource_uid(self):
        """Get the Tempo datasource uid.

        Returns the first related datasource that is a tempo datasource and has the same grafana uid as the known
        grafana.

        Will raise:
          ConfigurationBlockingError: If no applications are related, or applications are related but have sent only
                                       non-tempo datasources
          ConfigurationWaitingError: If a datasource is related, but has not yet provided data
        """
        try:
            grafana_metadata = self._get_grafana_configuration()
        except ConfigurationBlockingError as e:
            raise ConfigurationBlockingError(f"Tempo datasource is missing because Grafana source is missing.  Got"
                                              f" error: {e}")
        except ConfigurationWaitingError as e:
            raise ConfigurationWaitingError(f"Tempo datasource is missing because Grafana source is incomplete.  Got"
                                              f" error: {e}")

        if len(self.model.relations.get(TEMPO_DATASOURCE_EXCHANGE_RELAION_NAME, ())) == 0:
            raise ConfigurationBlockingError(f"No tempo available over the {TEMPO_DATASOURCE_EXCHANGE_RELAION_NAME} relation")

        tempo_datasources = self._tempo_datasource_exchange.received_datasources
        if len(tempo_datasources) == 0:
            ConfigurationWaitingError("Tempo datasource relation exists, but no data has been provided")

        for datasource in tempo_datasources:
            if datasource.type != "tempo":
                continue
            if datasource.grafana_uid == grafana_metadata.uid:
                return datasource.uid
        raise ConfigurationWaitingError("Tempo datasource relation exists, but it does not provide any tempo datasources.")

    def _is_prometheus_source_available(self):
        """Return True if the Prometheus source is available, else False."""
        try:
            self._get_prometheus_source_url()
            return True
        except ConfigurationError:
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


class ConfigurationError(Exception):
    """Base exception for configuration errors."""
    pass


class ConfigurationBlockingError(ConfigurationError):
    """Raised when a configuration error should result in a Blocked status."""
    pass


class ConfigurationWaitingError(ConfigurationError):
    """Raised when a configuration error should result in a Waiting status."""
    pass


if __name__ == "__main__":
    ops.main.main(KialiCharm)
