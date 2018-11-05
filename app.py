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

from thoth.common import init_logging
from thoth.common import get_service_account_token


_LOGGER = logging.getLogger("thoth.cleanup_job")

KUBERNETES_API_URL = os.getenv(
    "KUBERNETES_API_URL", "https://kubernetes.default.svc.cluster.local"
)
KUBERNETES_API_TOKEN = os.getenv("KUBERNETES_API_TOKEN") or get_service_account_token()
THOTH_MIDDLETIER_NAMESPACE = os.environ["THOTH_MIDDLETIER_NAMESPACE"]
THOTH_ANALYZER_CLEANUP_TIME = timeparse(os.getenv("THOTH_ANALYZER_CLEANUP_TIME", "7d"))


def _get_analyzers():
    """Get currently running analyzers."""
    # TODO: pagination?
    endpoint = "{}/{}/{}/{}".format(
        KUBERNETES_API_URL,
        "api/v1/namespaces",
        THOTH_MIDDLETIER_NAMESPACE,
        "pods?labelSelector=thothtype%3Duserpod",
    )
    response = requests.get(
        endpoint,
        verify=bool(int(os.getenv("KUBERNETES_VERIFY_TLS", True))),
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


def _get_jobs(label_selector: str) -> dict:
    """Get succeeded jobs by label."""
    # TODO: pagination?
    # TODO: if label_selector is empty do not use it for requests.get()
    endpoint = (
        f"{KUBERNETES_API_URL}/apis/batch/v1/namespaces/{THOTH_MIDDLETIER_NAMESPACE}/"
        f"jobs?labelSelector={label_selector}&limit=500"
    )  # we play nice and just get 500

    response = requests.get(
        endpoint,
        verify=bool(int(os.getenv("KUBERNETES_VERIFY_TLS", True))),
        headers={
            "Authorization": "Bearer {}".format(KUBERNETES_API_TOKEN),
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    _LOGGER.debug("Full response from Kubernetes master is: %r", response.json())

    jobs = response.json().get("items", [])
    _LOGGER.info(
        "Kubernetes master returned %r jobs with label %s", len(jobs), label_selector
    )

    return jobs


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
        verify=bool(os.getenv("KUBERNETES_VERIFY_TLS", True)),
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
            analyzer["metadata"]["creationTimestamp"]
        ).timestamp()
        if creation_time + lifetime <= now:
            _LOGGER.info("Deleting pod %r", analyzer["metadata"]["name"])
            try:
                _delete_pod(analyzer["metadata"]["name"])
            except Exception:
                _LOGGER.exception(
                    "Failed to delete pod {!r}, error is not fatal".format(
                        analyzer["metadata"]["name"]
                    )
                )
        else:
            _LOGGER.info("Keeping pod %r, not too old yet", pod["metadata"]["name"])


def _delete_object(api: str, type: str, name: str):
    """Delete Object of Type type within API Group api."""
    endpoint = f"{KUBERNETES_API_URL}/{api}/namespaces/{THOTH_MIDDLETIER_NAMESPACE}/{type}/{name}"

    response = requests.delete(
        endpoint,
        verify=False,
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


def main():
    """Perform cleanup of Kubernetes records."""
    init_logging()
    analyzers = _get_analyzers()
    _delete_old_analyzes(analyzers)


if __name__ == "__main__":
    _LOGGER.setLevel(
        logging.DEBUG if bool(int(os.getenv("CLEANUP_DEBUG", 0))) else logging.INFO
    )

    _LOGGER.info(f"Thoth Cleanup Job v{__version__} starting...")

    with _METRIC_RUNTIME.time():
        # let's delete all succeeded and outdated Solver Jobs
        jobs = _get_jobs("component%3Dsolver-f27")

        _delete_outdated_jobs(jobs)

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
