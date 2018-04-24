#!/usr/bin/env python3
"""Delete old analyzers in the cluster."""

import datetime
import logging
import os
import sys

from dateutil.parser import parse as datetime_parser
from pytimeparse.timeparse import timeparse
import requests

from thoth.common import init_logging


def _get_api_token():
    """Get token to Kubernetes master."""
    try:
        with open('/var/run/secrets/kubernetes.io/serviceaccount/token', 'r') as token_file:
            return token_file.read()
    except FileNotFoundError as exc:
        raise FileNotFoundError("Unable to get service account token, please check that service has "
                                "service account assigned with exposed token") from exc


_LOGGER = logging.getLogger('thoth.cleanup_job')

KUBERNETES_API_URL = os.getenv('KUBERNETES_API_URL', 'https://kubernetes.default.svc.cluster.local')
KUBERNETES_API_TOKEN = os.getenv('KUBERNETES_API_TOKEN') or _get_api_token()
THOTH_MIDDLEEND_NAMESPACE = os.environ['THOTH_MIDDLEEND_NAMESPACE']
THOTH_ANALYZER_CLEANUP_TIME = timeparse(os.getenv('THOTH_ANALYZER_CLEANUP_TIME', '7d'))


def _get_analyzers():
    """Get currently running analyzers."""
    # TODO: pagination?
    endpoint = "{}/{}/{}/{}".format(KUBERNETES_API_URL,
                                    'api/v1/namespaces',
                                    THOTH_MIDDLEEND_NAMESPACE,
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
                                    THOTH_MIDDLEEND_NAMESPACE,
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
            except:
                _LOGGER.exception("Failed to delete pod {!r}, error is not fatal".format(analyzer['metadata']['name']))
        else:
            _LOGGER.info("Keeping pod %r, not too old yet", analyzer['metadata']['name'])


def main():
    init_logging()
    analyzers = _get_analyzers()
    _delete_old_analyzes(analyzers)


if __name__ == '__main__':
    sys.exit(main())
