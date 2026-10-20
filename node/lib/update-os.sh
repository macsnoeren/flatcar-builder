#!/usr/bin/env bash
# update-os.sh — writes the bundle's Flatcar payload to the inactive partition.
#
# Flatcar has two /usr partitions; this writes the one that is NOT running and
# makes it the one to try at the next boot. Nothing that runs now is touched,
# and the disk is never wiped. install.sh reboots afterwards.
#
# flatcar-update unmasks update-engine to apply the payload and leaves it
# unmasked; a node built by Flatcar Builder never looks for an update by
# itself, so the mask goes back as soon as the payload is on disk.
set -euo pipefail

BUNDLE_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )"
PAYLOAD="${BUNDLE_DIR}/updates/flatcar_production_update.gz"
VERSION="$(tr -d '\r\n' < "${BUNDLE_DIR}/updates/TARGET_FLATCAR_VERSION")"

[[ -f "${PAYLOAD}" ]] || { echo "[x] no update payload: ${PAYLOAD}" >&2; exit 1; }
[[ -n "${VERSION}" ]] || { echo "[x] no target version in the bundle" >&2; exit 1; }

echo "[+] Flatcar OS update to ${VERSION}"
flatcar-update --to-version "${VERSION}" --to-payload "${PAYLOAD}"

systemctl stop update-engine >/dev/null 2>&1 || true
systemctl mask update-engine >/dev/null 2>&1 || true

echo "[+] ${VERSION} written to the inactive partition"
