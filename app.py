#!/usr/bin/env python3
# thoth-cleanup-job
# Copyright(C) 2018, 2019 Fridolin Pokorny
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

from dateutil.parser import parse as datetime_parser
import click
from pytimeparse import parse as parse_ttl
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
_DEFAULT_TTL = parse_ttl(os.getenv("THOTH_CLEANUP_DEFAULT_TTL") or "7d")
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
    "thoth_cleanup_job_pods",
    "Pods cleaned up.",
    ["namespace", "component", "resource"],
    registry=_PROMETHEUS_REGISTRY
)
_METRIC_DELETED_JOBS = Counter(
    "thoth_cleanup_jobs",
    "Jobs cleaned up.",
    ["namespace", "component", "resource"],
    registry=_PROMETHEUS_REGISTRY
)

_RESOURCES = frozenset(
    (
        # apiVersion, Type, delete based on creation (if false, take completionTime in status)
        ("build.openshift.io/v1", "BuildConfig", True, _METRIC_DELETED_BUILDCONFIGS),
        ("image.openshift.io/v1", "ImageStream", True, _METRIC_DELETED_IMAGESTREAMS),
        ("v1", "ConfigMap", True, _METRIC_DELETED_CONFIGMAPS),
        ("v1", "Pod", True, _METRIC_DELETED_PODS),
        ("batch/v1", "Job", False, _METRIC_DELETED_JOBS),
    )
)


def _do_cleanup(cleanup_namespace: str) -> None:
    """Perform the actual cleanup."""
    openshift = OpenShift()
    now = datetime.datetime.now(datetime.timezone.utc)

    for resource_version, resource_type, creation_delete, metric in _RESOURCES:
        resources = openshift.ocp_client.resources.get(api_version=resource_version, kind=resource_type)
        for item in resources.get(label_selector=_CLEANUP_LABEL_SELECTOR, namespace=cleanup_namespace).items:
            if item.status.phase == "Succeeded":
                _LOGGER.debug(
                    "Checking expiration of resource %r from namespace %r of kind %r",
                    item.metadata.name,
                    cleanup_namespace,
                    resources.kind,
                )

                ttl = item.metadata.labels.ttl
                try:
                    parsed_ttl = parse_ttl(ttl) if ttl else _DEFAULT_TTL
                except Exception as exc:
                    _LOGGER.exception(
                        "Failed to parse TTL %r for resource %r of type %r in namespace %r the object will not be "
                        "deleted",
                        ttl,
                        item.metadata.name,
                        resources.kind,
                        cleanup_namespace,
                    )
                    continue

                if creation_delete:
                    if not item.metadata.creationTimestamp:
                        _LOGGER.info(
                            "Skipping resource %r of type %r- no creation timestsamp found in metadata",
                            item.metadata.name,
                            resources.type,
                        )
                        continue
                    created_str = item.metadata.creationTimestamp
                else:
                    if not item.status.completionTime:
                        _LOGGER.info(
                            "Skipping resource %r of type %r- no completion time found in status field",
                            item.metadata.name,
                            resources.kind,
                        )
                        continue
                    created_str = item.status.completionTime

                created = datetime_parser(created_str)
                lived_for = (now - created).total_seconds()

                if lived_for > parsed_ttl:
                    _LOGGER.info(
                        "Deleting resource %r of type %r in namespace %r - created at %r",
                        item.metadata.name,
                        resources.kind,
                        cleanup_namespace,
                        created_str,
                    )
                    try:
                        resources.delete(name=item.metadata.name, namespace=cleanup_namespace)
                        metric.labels(
                            namespace=cleanup_namespace,
                            component=item.metadata.labels.component,
                            resource=resource_type
                        ).inc()
                    except Exception as exc:
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
                        parsed_ttl,
                    )
            else:
                _LOGGER.info(
                    "Skipping resource %r- at phase %r",
                    item.metadata.name,
                    item.status.phase,
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

    with _METRIC_RUNTIME.time():
        try:
            _do_cleanup(cleanup_namespace)
        finally:
            if _THOTH_METRICS_PUSHGATEWAY_URL:
                try:
                    _LOGGER.info("Submitting metrics to Prometheus pushgateway %r", _THOTH_METRICS_PUSHGATEWAY_URL)
                    push_to_gateway(_THOTH_METRICS_PUSHGATEWAY_URL, job="cleanup", registry=_PROMETHEUS_REGISTRY)
                except Exception as exc:
                    _LOGGER.exception("An error occurred pushing the metrics: %s", exc)


if __name__ == "__main__":
    sys.exit(cli())
