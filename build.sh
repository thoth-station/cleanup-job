#!/bin/sh

docker login -u $DOCKER_USER -p $DOCKER_PASS

docker build . -t fridex/thoth-cleanup-job
docker push fridex/thoth-cleanup-job
