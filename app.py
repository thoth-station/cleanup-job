#!/usr/bin/env python3
# thoth-cleanup-job
# Copyright(C) 2018, 2019, 2020 Fridolin Pokorny
#
# This program is free software: you can redistribute it and / or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

"""Delete old resources in the cluster."""


import datetime
import logging
import os
import sys
from typing import Optional
from typing import Any

from dateutil.parser import parse as datetime_parser
import click
from pytimeparse import parse as pytimeparse_parse
from prometheus_client import CollectorRegistry
from prometheus_client import Gauge
from prometheus_client import Counter
from prometheus_client import push_to_gateway

from thoth.common import init_logging
from thoth.common import OpenShift
from thoth.common import __version__ as __common__version__


__version__ = f"0.7.0+common.{__common__version__}"


init_logging()


_LOGGER = logging.getLogger("thoth.cleanup_job")
_DEFAULT_TTL = pytimeparse_parse(os.getenv("THOTH_CLEANUP_DEFAULT_TTL") or "2h")
_CLEANUP_LABEL_SELECTOR = "mark=cleanup"
_PROMETHEUS_REGISTRY = CollectorRegistry()
_THOTH_METRICS_PUSHGATEWAY_URL = os.getenv("PROMETHEUS_PUSHGATEWAY_URL")
_METRIC_RUNTIME = Gauge(
    "thoth_cleanup_job_runtime_seconds", "Runtime of cleanup job in seconds.", [], registry=_PROMETHEUS_REGISTRY
)
_METRIC_INFO = Gauge(
    "thoth_cleanup_job_info", "Thoth Cleanup Job information", ["version"], registry=_PROMETHEUS_REGISTRY
)

_METRIC_DELETED_BUILDCONFIGS = Counter(
    "thoth_cleanup_job_buildconfigs",
    "Buildconfigs cleaned up.",
    ["namespace", "component", "resource"],
    registry=_PROMETHEUS_REGISTRY,
)
_METRIC_DELETED_IMAGESTREAMS = Counter(
    "thoth_cleanup_job_imagestreams",
    "Imagestreams cleaned up.",
    ["namespace", "component", "resource"],
    registry=_PROMETHEUS_REGISTRY,
)
_METRIC_DELETED_CONFIGMAPS = Counter(
    "thoth_cleanup_job_configmaps",
    "Configmaps cleaned up.",
    ["namespace", "component", "resource"],
    registry=_PROMETHEUS_REGISTRY,
)
_METRIC_DELETED_PODS = Counter(
    "thoth_cleanup_job_pods", "Pods cleaned up.", ["namespace", "component", "resource"], registry=_PROMETHEUS_REGISTRY
)
_METRIC_DELETED_WORKFLOWS = Counter(
    "thoth_cleanup_job_workflows",
    "Workflows cleaned up.",
    ["namespace", "component", "resource"],
    registry=_PROMETHEUS_REGISTRY
)
_METRIC_DELETED_JOBS = Counter(
    "thoth_cleanup_jobs", "Jobs cleaned up.", ["namespace", "component", "resource"], registry=_PROMETHEUS_REGISTRY
)


def _creation_based_delete(item: Any, resources: Any, cleanup_namespace: str, metric: Any) -> None:
    """Delete the given object based on creation time."""
    now = datetime.datetime.now(datetime.timezone.utc)

    created = datetime_parser(item.metadata.creationTimestamp)
    lived_for = (now - created).total_seconds()
    ttl = _parse_ttl(item.metadata.labels.ttl)

    if lived_for > ttl:
        _LOGGER.info(
            "Deleting resource %r of type %r in namespace %r - created at %r",
            item.metadata.name,
            resources.kind,
            cleanup_namespace,
            item.metadata.creationTimestamp,
        )
        try:
            resources.delete(name=item.metadata.name, namespace=cleanup_namespace)
            metric.labels(
                namespace=cleanup_namespace,
                component=item.metadata.labels.component,
                resource="BuildConfig",
            ).inc()
        except Exception:
            _LOGGER.exception(
                "Failed to delete resource %r of type %r in namespace %r",
                item.metadata.name,
                resources.kind,
                cleanup_namespace,
            )
    else:
        _LOGGER.info(
            "Keeping resource %r of type %r in namespace %r ttl not expired yet (lived for %r, ttl is %r)",
            item.metadata.name,
            resources.kind,
            cleanup_namespace,
            lived_for,
            ttl,
        )


def _parse_ttl(field: Optional[str] = None) -> Optional[int]:
    """Parse time-to-live (TTL) field in carried in the object."""
    try:
        return pytimeparse_parse(field) if field else _DEFAULT_TTL
    except Exception:
        _LOGGER.exception(
            "Failed to parse TTL %r",
            field
        )
        return None


def _cleanup_job(openshift: OpenShift, cleanup_namespace: str) -> None:
    """Cleanup resource of type job."""
    now = datetime.datetime.now(datetime.timezone.utc)

    _LOGGER.info("Cleaning old resources of type job")
    resources = openshift.ocp_client.resources.get(api_version="batch/v1", kind="Job")
    for item in resources.get(label_selector=_CLEANUP_LABEL_SELECTOR, namespace=cleanup_namespace).items:
        if not item.status.succeeded == 1:
            _LOGGER.info("Skipping %r as it has not been completed successfully", item.metadata.name)
            continue

        if not item.status.completionTime:
            _LOGGER.info(
                "Skipping resource %r of type %r- no completion time found in status field",
                item.metadata.name,
                resources.kind,
            )
            continue

        completed = datetime_parser(item.status.completionTime)
        lived_for = (now - completed).total_seconds()
        ttl = _parse_ttl(item.metadata.labels.ttl)

        if lived_for > ttl:
            _LOGGER.info(
                "Deleting resource %r of type %r in namespace %r - created at %r",
                item.metadata.name,
                resources.kind,
                cleanup_namespace,
                item.metadata.creationTimestamp,
            )
            try:
                resources.delete(name=item.metadata.name, namespace=cleanup_namespace)
                _METRIC_DELETED_JOBS.labels(
                    namespace=cleanup_namespace,
                    component=item.metadata.labels.component,
                    resource="Job",
                ).inc()
            except Exception:
                _LOGGER.exception(
                    "Failed to delete resource %r of type %r in namespace %r",
                    item.metadata.name,
                    resources.kind,
                    cleanup_namespace,
                )
        else:
            _LOGGER.info(
                "Keeping resource %r of type %r in namespace %r ttl not expired yet (lived for %r, ttl is %r)",
                item.metadata.name,
                resources.kind,
                cleanup_namespace,
                lived_for,
                ttl,
            )


def _cleanup_buildconfig(openshift: OpenShift, cleanup_namespace: str) -> None:
    """Cleanup resource of type buildconfig."""
    _LOGGER.info("Cleaning old resources of type buildconfig")
    resources = openshift.ocp_client.resources.get(api_version="build.openshift.io/v1", kind="BuildConfig")
    for item in resources.get(label_selector=_CLEANUP_LABEL_SELECTOR, namespace=cleanup_namespace).items:
        _creation_based_delete(item, resources, cleanup_namespace, _METRIC_DELETED_BUILDCONFIGS)


def _cleanup_imagestream(openshift: OpenShift, cleanup_namespace: str) -> None:
    """Cleanup resource of type imagestream."""
    _LOGGER.info("Cleaning old resources of type imagestream")
    resources = openshift.ocp_client.resources.get(api_version="image.openshift.io/v1", kind="ImageStream")
    for item in resources.get(label_selector=_CLEANUP_LABEL_SELECTOR, namespace=cleanup_namespace).items:
        _creation_based_delete(item, resources, cleanup_namespace, _METRIC_DELETED_IMAGESTREAMS)


def _cleanup_configmap(openshift: OpenShift, cleanup_namespace: str) -> None:
    """Cleanup resource of type configmap."""
    _LOGGER.info("Cleaning old resources of type configmap")
    resources = openshift.ocp_client.resources.get(api_version="v1", kind="ConfigMap")
    for item in resources.get(label_selector=_CLEANUP_LABEL_SELECTOR, namespace=cleanup_namespace).items:
        _creation_based_delete(item, resources, cleanup_namespace, _METRIC_DELETED_CONFIGMAPS)


def _cleanup_pod(openshift: OpenShift, cleanup_namespace: str) -> None:
    """Cleanup resource of type pod."""
    now = datetime.datetime.now(datetime.timezone.utc)

    _LOGGER.info("Cleaning old resources of type pod")
    resources = openshift.ocp_client.resources.get(api_version="v1", kind="Pod")
    for item in resources.get(label_selector=_CLEANUP_LABEL_SELECTOR, namespace=cleanup_namespace).items:
        if item.status.phase != 'Succeeded':
            _LOGGER.info("Skipping %r as it has not been successful", item.metadata.name)
            continue

        ttl = _parse_ttl(item.metadata.labels.ttl)

        for container_status in item.status.containerStatuses:
            finished = datetime_parser(container_status.state.terminated.finishedAt)
            lived_for = (now - finished).total_seconds()

            if lived_for < ttl:
                _LOGGER.info(
                    "Skipping %r of type %r in namespace %r as finished containers lived"
                    "for %r and did not exceeded ttl %r",
                    item.metadata.name,
                    resources.kind,
                    cleanup_namespace,
                    lived_for,
                    ttl,
                 )
                break
        else:
            _LOGGER.info(
                "Deleting pod %r in namespace %r, created at %r - pod should be deleted based on ttl %r",
                item.metadata.name,
                cleanup_namespace,
                item.metadata.creationTimestamp,
                ttl
            )
            try:
                resources.delete(name=item.metadata.name, namespace=cleanup_namespace)
                _METRIC_DELETED_PODS.labels(
                    namespace=cleanup_namespace,
                    component=item.metadata.labels.component,
                    resource="Pod",
                ).inc()
            except Exception:
                _LOGGER.exception(
                    "Failed to delete resource %r of type %r in namespace %r",
                    item.metadata.name,
                    resources.kind,
                    cleanup_namespace,
                )


def _cleanup_workflows(openshift: OpenShift, cleanup_namespace: str) -> None:
    """Clean up finished Argo workflows if Argo does not clean them up."""
    now = datetime.datetime.now(datetime.timezone.utc)

    _LOGGER.info("Cleaning old Argo workflows")
    resources = openshift.ocp_client.resources.get(api_version="argoproj.io/v1alpha1", kind="Workflow")
    for item in resources.get(namespace=cleanup_namespace).items:
        if item.status.finishedAt is None:
            _LOGGER.info("Skipping %r as it is not finished yet", item.metadata.name)
            continue

        ttl = _parse_ttl(item.metadata.labels.ttl)
        finished = datetime_parser(item.status.finishedAt)
        lived_for = (now - finished).total_seconds()

        if lived_for < ttl:
            _LOGGER.info(
                "Skipping %r of type %r in namespace %r as workflow lived"
                "for %r and did not exceeded ttl %r",
                item.metadata.name,
                resources.kind,
                cleanup_namespace,
                lived_for,
                ttl,
             )
            continue

        _LOGGER.info(
            "Deleting workflow %r in namespace %r, created at %r",
            item.metadata.name,
            cleanup_namespace,
            item.metadata.creationTimestamp,
        )

        try:
            resources.delete(name=item.metadata.name, namespace=cleanup_namespace)
            _METRIC_DELETED_WORKFLOWS.labels(
                namespace=cleanup_namespace,
                component=item.metadata.labels.component,
                resource="Workflow",
            ).inc()
        except Exception:
            _LOGGER.exception(
                "Failed to delete resource %r of type %r in namespace %r",
                item.metadata.name,
                resources.kind,
                cleanup_namespace,
            )


_CLEANUP_HANDLERS = (
    _cleanup_job,
    _cleanup_buildconfig,
    _cleanup_imagestream,
    _cleanup_configmap,
    _cleanup_pod,
    _cleanup_workflows,
)


@click.command()
@click.option("--verbose", is_flag=True, envvar="THOTH_CLEANUP_VERBOSE", help="Be verbose about what is going on.")
@click.option(
    "--cleanup-namespace",
    type=str,
    required=True,
    envvar="THOTH_CLEANUP_NAMESPACE",
    help="Namespace in which cleanups should be done.",
)
def cli(cleanup_namespace: str, verbose: bool = False):
    """Operator handling Thoth's workloads."""
    if verbose:
        _LOGGER.setLevel(logging.DEBUG)

    _LOGGER.info("Thoth Cleanup Job v%s starting...", __version__)
    _LOGGER.info("Cleanup will be performed in namespace %r", cleanup_namespace)
    _METRIC_INFO.labels(__version__).inc()

    openshift = OpenShift()

    with _METRIC_RUNTIME.time():
        try:
            for cleanup_handler in _CLEANUP_HANDLERS:
                cleanup_handler(openshift, cleanup_namespace)
        finally:
            if _THOTH_METRICS_PUSHGATEWAY_URL:
                try:
                    _LOGGER.info("Submitting metrics to Prometheus pushgateway %r", _THOTH_METRICS_PUSHGATEWAY_URL)
                    push_to_gateway(_THOTH_METRICS_PUSHGATEWAY_URL, job="cleanup", registry=_PROMETHEUS_REGISTRY)
                except Exception as exc:
                    _LOGGER.exception("An error occurred pushing the metrics: %s", exc)


if __name__ == "__main__":
    sys.exit(cli())
