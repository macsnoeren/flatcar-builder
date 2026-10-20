#!/usr/bin/env bash
# install-flatcar.sh — installs Flatcar for one node on an EMPTY machine.
#
# Run it AS ROOT from a rescue system or a live USB:
#   bash scripts/install-flatcar.sh <node> [device]
#
# flatcar-install repartitions the WHOLE disk. This is for a first install
# only; an installed node is updated with a bundle, over its A/B partitions.
set -euo pipefail

USB_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )"

nodes() { find "${USB_DIR}/nodes" -mindepth 1 -maxdepth 1 -type d -printf '  %f\n' | sort >&2; }

NODE="${1:-}"
if [[ -z "${NODE}" ]]; then
  echo "Usage: bash install-flatcar.sh <node> [device]" >&2
  echo "Nodes on this USB:" >&2
  nodes
  exit 1
fi
NODE_DIR="${USB_DIR}/nodes/${NODE}"
if [[ ! -d "${NODE_DIR}" ]]; then
  echo "Unknown node: ${NODE}. Nodes on this USB:" >&2
  nodes
  exit 1
fi

# Root is checked once, up front: a run that has root for some steps and not
# others leaves a wiped disk with half an installation on it. A rescue system
# is usually root already and often has no sudo at all.
if [[ "${EUID}" -ne 0 ]]; then
  echo "install-flatcar.sh must run as root; you are uid ${EUID}. Become root ('su -' or 'sudo -i')." >&2
  exit 1
fi

# shellcheck source=/dev/null
. "${NODE_DIR}/node.env"
DEVICE="${2:-${INSTALL_DEVICE}}"

echo "[+] Installing Flatcar on ${DEVICE} for ${NODE} (${HOSTNAME}) — this wipes ${DEVICE}"
bash "${USB_DIR}/flatcar/flatcar-install" \
  -d "${DEVICE}" \
  -f "${USB_DIR}/flatcar/flatcar_production_image.bin.bz2" \
  -i "${NODE_DIR}/config.ign"

echo "[+] Copying the docker images and docker-compose onto the new root"
mnt="$(mktemp -d)"
# Partition 9 is ROOT; NVMe and MMC devices name it p9.
mount "${DEVICE}9" "${mnt}" 2>/dev/null || mount "${DEVICE}p9" "${mnt}"
trap 'umount "${mnt}"; rmdir "${mnt}"' EXIT
mkdir -p "${mnt}/opt/images" "${mnt}/opt/bin"
while IFS= read -r tarname; do
  [[ -n "${tarname}" ]] || continue
  echo "  ${tarname}"
  cp "${USB_DIR}/docker-images/${tarname}" "${mnt}/opt/images/"
done < "${NODE_DIR}/images.list"
cp "${USB_DIR}/bin/docker-compose" "${mnt}/opt/bin/docker-compose"
chmod 0755 "${mnt}/opt/bin/docker-compose"

echo "[+] Done. Reboot the machine. Its secrets arrive with the first app bundle."
