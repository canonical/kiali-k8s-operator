"""Test helpers for the Tempo integration tests.

These are slightly modified from https://github.com/canonical/tempo-coordinator-k8s-operator/tree/main/tests/integration
"""

import copy
from dataclasses import asdict

from minio import Minio
from ops import Application
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import CharmDeploymentConfiguration

TEMPO_COORDINATOR_K8S = CharmDeploymentConfiguration(
    entity_url="tempo-coordinator-k8s",
    application_name="tempo-coordinator-k8s",
    channel="2/edge",
    trust=True,
)

TEMPO_WORKER_K8S = CharmDeploymentConfiguration(
    entity_url="tempo-worker-k8s",
    application_name="tempo-worker-k8s",
    channel="2/edge",
    trust=True,
)

S3_INTEGRATOR = CharmDeploymentConfiguration(
    entity_url="s3-integrator",
    application_name="s3-integrator",
    channel="2/edge",
    trust=False,
)

MINIO = CharmDeploymentConfiguration(
    entity_url="minio",
    application_name="minio",
    channel="latest/edge",
    trust=True,
)

ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
S3_CREDENTIALS = {
    "access-key": ACCESS_KEY,
    "secret-key": SECRET_KEY,
}



async def deploy_monolithic_cluster(ops_test: OpsTest):
    """Deploy a monolithic tempo cluster."""
    coordinator_name = TEMPO_COORDINATOR_K8S.application_name
    worker_name = TEMPO_WORKER_K8S.application_name
    s3_integrator_name = S3_INTEGRATOR.application_name

    await ops_test.model.deploy(**asdict(TEMPO_COORDINATOR_K8S))
    await ops_test.model.deploy(**asdict(TEMPO_WORKER_K8S))
    await ops_test.model.deploy(**asdict(S3_INTEGRATOR))

    await deploy_and_configure_minio(
        s3_integrator=s3_integrator_name,
        bucket_name="tempo",
        ops_test=ops_test,
    )

    await ops_test.model.integrate(
        coordinator_name + ":s3", s3_integrator_name + ":s3-credentials"
    )
    await ops_test.model.integrate(
        coordinator_name + ":tempo-cluster", worker_name + ":tempo-cluster"
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[coordinator_name, worker_name, s3_integrator_name],
            status="active",
            timeout=2000,
            idle_period=30,
        )

    return coordinator_name


async def deploy_and_configure_minio(
    s3_integrator, bucket_name, ops_test: OpsTest
):
    """Deploy and configure minio for tempo."""
    minio_name = MINIO.application_name
    minio_with_config = copy.deepcopy(MINIO)
    minio_with_config.config = S3_CREDENTIALS

    await ops_test.model.deploy(**asdict(minio_with_config))
    await ops_test.model.wait_for_idle(apps=[minio_name], status="active", timeout=2000)
    minio_addr = await get_unit_address(ops_test, minio_name, "0")

    mc_client = Minio(
        f"{minio_addr}:9000",
        **{key.replace("-", "_"): value for key, value in S3_CREDENTIALS.items()},
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(bucket_name)
    if not found:
        mc_client.make_bucket(bucket_name)

    # configure s3-integrator
    s3_integrator_app: Application = ops_test.model.applications[s3_integrator]

    secret_uri = await ops_test.juju(
        "add-secret",
        f"{s3_integrator_app}-creds",
        *(f"{key}={val}" for key, val in S3_CREDENTIALS.items()),
    )
    await ops_test.juju("grant-secret", f"{s3_integrator_app}-creds", s3_integrator_app)

    await s3_integrator_app.set_config(
        {
            "endpoint": f"minio-0.minio-endpoints.{ops_test.model.name}.svc.cluster.local:9000",
            "bucket": bucket_name,
            "credentials": secret_uri.strip(),
        }
    )


async def get_unit_address(ops_test: OpsTest, app_name, unit_no):
    """Return the address of the unit with the given name and unit number."""
    status = await ops_test.model.get_status()
    app = status["applications"][app_name]
    if app is None:
        assert False, f"no app exists with name {app_name}"
    unit = app["units"].get(f"{app_name}/{unit_no}")
    if unit is None:
        assert False, f"no unit exists in app {app_name} with index {unit_no}"
    return unit["address"]
