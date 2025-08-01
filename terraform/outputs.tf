output "app_name" {
  value = juju_application.this_app.name
}

output "endpoints" {
  value = {
    # Requires
    grafana-metadata = "grafana-metadata"
    ingress          = "ingress"
    istio-metadata   = "istio-metadata"
    logging          = "logging"
    prometheus-api   = "prometheus-api"
    require-cmr-mesh = "require-cmr-mesh"
    service-mesh     = "service-mesh"
    tempo-api        = "tempo-api"

    # Provides
    metrics-endpoint          = "metrics-endpoint"
    provide-cmr-mesh          = "provide-cmr-mesh"
    tempo-datasource-exchange = "tempo-datasource-exchange"
  }
}
