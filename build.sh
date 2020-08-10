#!/bin/bash
set -ex

# This script is the Jenkins build helper as it were. 

GIT_SHA=$(git rev-parse HEAD | cut -c 1-8)
# eval $(/usr/local/bin/aws ecr get-login --no-include-email --profile=ecr-user --region=us-east-2)

IMAGE="jeremykuhnash/targetd:$GIT_SHA"
IMAGE_LATEST="jeremykuhnash/targetd:latest"
docker build -t $IMAGE -f docker/Dockerfile .
docker push $IMAGE
docker push $IMAGE_LATEST