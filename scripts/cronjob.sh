#!/bin/bash
set -ex

pushd $(dirname "$(dirname "${BASH_SOURCE[0]}")")

docker build -t marketbot-gold .
docker run -it --rm marketbot-gold

popd
