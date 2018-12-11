thoth-cleanup-job
-----------------

A job for cleaning up old OpenShift or Kubernetes objects created by one-time analyses in Thoth.

This periodic job (CronJob) queries Jobs, ImageStreams, ConfigMaps and BuildConfigs that have label set to `mark=cleanup` and based on TTL, old objects are deleted. The TTL can be provided in the following ways:

* using `THOTH_CLEANUP_DEFAULT_TTL` environment variable provided to the thoth-cleanup CronJob deployment
* in the label under the `ttl` key - for example in the job spec label `ttl=5d` means "delete the given job after 5 days" - this overrides the above configuration option

If there is no ttl set in the resourse labels and there is no `THOTH_CLEANUP_DEFAULT_TTL` set in the deployement, the TTL configuration defaults to 7 days.

