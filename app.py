#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# thoth-cleanup-job
# Copyright(C) 2018 Fridolin Pokorny
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

"""Delete old analyzers in the cluster."""


import datetime
import logging
import os
import sys

from dateutil.parser import parse as datetime_parser
from pytimeparse.timeparse import timeparse
import requests

from thoth.common import init_logging, get_service_account_token, OpenShift
from thoth.common import __version__ as __common__version__
from thoth.common.exceptions import NotFoundException

__version__ = f"0.6.2+common.{__common__version__}"


init_logging()
prometheus_registry = CollectorRegistry()

_LOGGER = logging.getLogger("thoth.cleanup_job")
_OPENSHIFT = OpenShift()


KUBERNETES_API_URL = os.getenv(
    "KUBERNETES_API_URL", "https://kubernetes.default.svc.cluster.local"
)
KUBERNETES_API_TOKEN = os.getenv("KUBERNETES_API_TOKEN") or get_service_account_token()
KUBERNETES_VERIFY_TLS = bool(int(os.getenv("KUBERNETES_VERIFY_TLS", 0)))
THOTH_MIDDLETIER_NAMESPACE = os.environ["THOTH_MIDDLETIER_NAMESPACE"]
THOTH_CLEANUP_TIMEOUT = timeparse(os.getenv("THOTH_CLEANUP_TIMEOUT", "5d"))
THOTH_METRICS_PUSHGATEWAY_URL = os.getenv("THOTH_METRICS_PUSHGATEWAY_URL")
THOTH_MY_NAMESPACE = os.getenv("NAMESPACE", "thoth-test-core")

_METRIC_RUNTIME = Gauge(
    "thoth_cleanup_job_runtime_seconds",
    "Runtime of cleanup job in seconds.",
    [],
    registry=prometheus_registry,
)
_METRIC_SOLVER_JOBS = Counter(
    "thoth_cleanup_job_solver_jobs",
    "Solver Jobs cleaned up.",
    ["env", "op"],
    registry=prometheus_registry,
)

# Metrics Exporter Metrics
_METRIC_INFO = Gauge(
    "thoth_cleanup_job_info",
    "Thoth Cleanup Job information",
    ["env", "version"],
    registry=prometheus_registry,
)
_METRIC_INFO.labels(THOTH_MY_NAMESPACE, __version__).inc()


def _get_pods(label_selector: str) -> dict:
    """Get currently running analyzers by label."""
    # TODO: pagination?
    endpoint = (
        f"{KUBERNETES_API_URL}/api/v1/namespaces/{THOTH_MIDDLETIER_NAMESPACE}/"
        f"pods?labelSelector={label_selector}"
    )

    response = requests.get(
        endpoint,
        verify=KUBERNETES_VERIFY_TLS,
        headers={
            "Authorization": "Bearer {}".format(KUBERNETES_API_TOKEN),
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    _LOGGER.debug("Full response from Kubernetes master is: %r", response.json())

    pods = response.json().get("items", [])
    _LOGGER.info(
        "Kubernetes master returned %r pods with label %r", len(pods), label_selector
    )

    return pods


def _delete_pod(pod_name):
    endpoint = "{}/{}/{}/{}".format(
        KUBERNETES_API_URL,
        "api/v1/namespaces",
        THOTH_MIDDLETIER_NAMESPACE,
        "pods",
        pod_name,
    )
    response = requests.delete(
        endpoint,
        verify=KUBERNETES_VERIFY_TLS,
        headers={
            "Authorization": "Bearer {}".format(KUBERNETES_API_TOKEN),
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    _LOGGER.debug("Response from Kubernetes master on deletion is: %r", response.json())

    return (pod for pod in response.json()["items"])


def _delete_old_analyzes(analyzers):
    """Delete old analyzers."""
    now = datetime.datetime.now().timestamp()
    lifetime = datetime.timedelta(seconds=THOTH_ANALYZER_CLEANUP_TIME).total_seconds()

    for analyzer in analyzers:
        # TODO: also delete pods where pull failed
        creation_time = datetime_parser(
            pod["metadata"]["creationTimestamp"]
        ).timestamp()
        if creation_time + lifetime <= now:
            _LOGGER.info("Deleting pod %r", pod["metadata"]["name"])
            try:
                _delete_pod(pod["metadata"]["name"])
            except Exception:
                _LOGGER.exception(
                    "Failed to delete pod {!r}, error is not fatal".format(
                        pod["metadata"]["name"]
                    )
                )
        else:
            _LOGGER.info("Keeping pod %r, not too old yet", pod["metadata"]["name"])


def _delete_object(api: str, kind: str, name: str):
    """Delete Object of Kind kind within API Group api."""
    endpoint = f"{KUBERNETES_API_URL}/{api}/namespaces/{THOTH_MIDDLETIER_NAMESPACE}/{kind}/{name}"

    response = requests.delete(
        endpoint,
        verify=KUBERNETES_VERIFY_TLS,
        headers={
            "Authorization": "Bearer {}".format(KUBERNETES_API_TOKEN),
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    _LOGGER.debug("Response from Kubernetes is: %r", response.json())


def _delete_outdated_jobs(jobs: dict):
    """Delete succeeded and outdated Jobs."""
    now = datetime.datetime.now().timestamp()
    lifetime = datetime.timedelta(seconds=THOTH_CLEANUP_TIMEOUT).total_seconds()

    for ding in jobs:
        creation_time = datetime_parser(
            ding["metadata"]["creationTimestamp"]
        ).timestamp()

        if creation_time + lifetime <= now:
            _LOGGER.debug("deleting Job %r", ding["metadata"]["name"])
            try:
                _delete_object("apis/batch/v1", "jobs", ding["metadata"]["name"])
            except Exception:
                _LOGGER.exception(
                    "Failed to delete Job {!r}, error is not fatal".format(
                        ding["metadata"]["name"]
                    )
                )

            _METRIC_SOLVER_JOBS.labels(THOTH_MY_NAMESPACE, "delete").inc()
        else:
            _LOGGER.debug("keeping Job %r, it's to young", ding["metadata"]["name"])
            _METRIC_SOLVER_JOBS.labels(THOTH_MY_NAMESPACE, "noop").inc()


if __name__ == "__main__":
    _LOGGER.setLevel(
        logging.DEBUG if bool(int(os.getenv("CLEANUP_DEBUG", 0))) else logging.INFO
    )

    _LOGGER.info(f"Thoth Cleanup Job v{__version__} starting...")
    _LOGGER.debug("running with DEBUG log level")

    with _METRIC_RUNTIME.time():
        try:
            # let's delete all succeeded and outdated Solver Jobs
            jobs = get_jobs("component%3Dsolver-f27", "thoth-test-core")

            _delete_outdated_jobs(jobs)
        except NotFoundException:
            pass

    if THOTH_METRICS_PUSHGATEWAY_URL:
        try:
            _LOGGER.debug(
                "Submitting metrics to Prometheus pushgateway %r",
                THOTH_METRICS_PUSHGATEWAY_URL,
            )
            push_to_gateway(
                THOTH_METRICS_PUSHGATEWAY_URL,
                job="cleanup",
                registry=prometheus_registry,
            )
        except Exception as e:
            _LOGGER.exception("An error occurred pushing the metrics: {!r}".format(e))
