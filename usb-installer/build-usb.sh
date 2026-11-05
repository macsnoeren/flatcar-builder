#!/bin/bash
#
# Build the maintenance USB image into output/, from any directory.
#
# Needs docker. The container is privileged, because the build creates loop
# devices and mounts filesystems through the host kernel.
#
# live-build keeps its bootstrap stage and downloaded packages in a docker
# volume. It is a volume and not a directory because the bootstrap stage is a
# complete Debian tree owned by root. Remove it to force a clean build:
#
#     docker volume rm flatcar-maintenance-cache
#
# EFI_SIZE_MB, LIVE_EXTRA_MB and SMOKE_TEST are passed on when they are set; see
# build-usb-image.sh.
#

set -euo pipefail

HERE="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

mkdir -p "${HERE}/output"

docker build -t flatcar-maintenance-builder "${HERE}"

docker run --rm --privileged \
    -e OWNER="$(id -u):$(id -g)" \
    -e EFI_SIZE_MB -e LIVE_EXTRA_MB -e SMOKE_TEST \
    -v "${HERE}/output:/output" \
    -v flatcar-maintenance-cache:/build/cache \
    flatcar-maintenance-builder
