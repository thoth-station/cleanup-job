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


_LOGGER = logging.getLogger('thoth.cleanup_job')

KUBERNETES_API_URL = os.getenv('KUBERNETES_API_URL', 'https://kubernetes.default.svc.cluster.local')
KUBERNETES_API_TOKEN = os.getenv('KUBERNETES_API_TOKEN') or get_service_account_token()
THOTH_MIDDLETIER_NAMESPACE = os.environ['THOTH_MIDDLETIER_NAMESPACE']
THOTH_ANALYZER_CLEANUP_TIME = timeparse(os.getenv('THOTH_ANALYZER_CLEANUP_TIME', '7d'))


def _get_analyzers():
    """Get currently running analyzers."""
    # TODO: pagination?
    endpoint = "{}/{}/{}/{}".format(KUBERNETES_API_URL,
                                    'api/v1/namespaces',
                                    THOTH_MIDDLETIER_NAMESPACE,
                                    'pods?labelSelector=thothtype%3Duserpod')
    response = requests.get(
        endpoint,
        verify=bool(int(os.getenv('KUBERNETES_VERIFY_TLS', True))),
        headers={
            'Authorization': 'Bearer {}'.format(KUBERNETES_API_TOKEN),
            'Content-Type': 'application/json'
        }
    )
    response.raise_for_status()
    _LOGGER.debug('Full response from Kubernetes master is: %r', response.json())
    pods = response.json().get('items', [])
    _LOGGER.info('Kubernetes master returned %d pods', len(pods))
    return pods


def _delete_pod(pod_name):
    endpoint = "{}/{}/{}/{}".format(KUBERNETES_API_URL,
                                    'api/v1/namespaces',
                                    THOTH_MIDDLETIER_NAMESPACE,
                                    'pods',
                                    pod_name)
    response = requests.delete(
        endpoint,
        verify=bool(os.getenv('KUBERNETES_VERIFY_TLS', True)),
        headers={
            'Authorization': 'Bearer {}'.format(KUBERNETES_API_TOKEN),
            'Content-Type': 'application/json'
        }
    )
    response.raise_for_status()
    _LOGGER.debug('Response from Kubernetes master on deletion is: %r', response.json())
    return (pod for pod in response.json()['items'])


def _delete_old_analyzes(analyzers):
    """Delete old analyzers."""
    now = datetime.datetime.now().timestamp()
    lifetime = datetime.timedelta(seconds=THOTH_ANALYZER_CLEANUP_TIME).total_seconds()

    for analyzer in analyzers:
        # TODO: also delete pods where pull failed
        creation_time = datetime_parser(analyzer['metadata']['creationTimestamp']).timestamp()
        if creation_time + lifetime <= now:
            _LOGGER.info("Deleting pod %r", analyzer['metadata']['name'])
            try:
                _delete_pod(analyzer['metadata']['name'])
            except Exception:
                _LOGGER.exception("Failed to delete pod {!r}, error is not fatal".format(analyzer['metadata']['name']))
        else:
            _LOGGER.info("Keeping pod %r, not too old yet", analyzer['metadata']['name'])


def main():
    """Perform cleanup of Kubernetes records."""
    init_logging()
    analyzers = _get_analyzers()
    _delete_old_analyzes(analyzers)



if __name__ == '__main__':
    sys.exit(main())
