thoth-cleanup-job
-----------------

A job for cleaning up old OpenShift or Kubernetes objects created by one-time analyses in Thoth.

This periodic job (CronJob) queries Jobs, ImageStreams, ConfigMaps and BuildConfigs that have label set to `mark=cleanup` and based on TTL, old objects are deleted. The TTL can be provided in the following ways:

* using `THOTH_CLEANUP_DEFAULT_TTL` environment variable provided to the thoth-cleanup CronJob deployment
* in the label under the `ttl` key - for example in the job spec label `ttl=5d` means "delete the given job after 5 days" - this overrides the above configuration option

If there is no ttl set in the resourse labels and there is no `THOTH_CLEANUP_DEFAULT_TTL` set in the deployement, the TTL configuration defaults to 7 days.

Running cleanup-job locally
===========================

You can run cleanup-job locally from your laptop. It will transparently talk to
the cluster - assuming you are logged in and have sufficient privileges on the
cleaned namespace (edit is required):

.. code-block:: console

   $ pipenv install   # Install all the requirements
   $ oc login <cluster-url>  # Make sure you are logged in to the cluster.
   $ KUBERNETES_VERIFY_TLS=0 pipenv run python3 app.py --cleanup-namespace thoth-test-core

TLS verification is skipped if cluster certs should not be checked (unsafe).

