#!/usr/bin/env bash
# install.sh — entrypoint of a bundle; watch-install.sh runs it from the root of
# the extracted archive.
#
#   bash install.sh [--dry-run]
#
#   1. checks: checksums, the bundle is for THIS node, and a bundle of selected
#      apps only updates apps that are already here;
#   2. switches Flatcar's own updates off (again);
#   3. lib/update-app.sh — the apps, without a reboot;
#   4. mode os: lib/update-os.sh to the inactive partition, then reboot.
#
# An app bundle never reboots the node. Everything is logged to
# /var/log/install.log as well as to stdout.
#
# ROOT (default empty) prefixes every path on the node, for tests.
set -euo pipefail

BUNDLE_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
ROOT="${ROOT:-}"
LOG="${ROOT}/var/log/install.log"
STATE="${ROOT}/var/lib/flatcar-builder"

DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "usage: install.sh [--dry-run]" >&2; exit 2 ;;
  esac
done

mkdir -p "$(dirname "${LOG}")"
exec > >(tee -a "${LOG}") 2>&1

log()  { printf '%s [install] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"; }
fail() { log "ERROR: $*"; exit 1; }

# shellcheck source=/dev/null
. "${BUNDLE_DIR}/bundle.env"
log "bundle ${BUNDLE}: node ${NODE}, mode ${MODE}$( [[ "${PARTIAL}" == 1 ]] && echo ", apps ${APPS}" )"

# 1. Checks, before anything changes.
( cd "${BUNDLE_DIR}" && sha256sum --quiet -c checksums.sha256 ) \
  || fail "the bundle does not match its checksums; nothing was changed"

if [[ -f "${ROOT}/etc/flatcar-builder/node.env" ]]; then
  # shellcheck source=/dev/null
  here="$(set -a; . "${ROOT}/etc/flatcar-builder/node.env"; printf '%s' "${NODE_NAME:-}")"
  [[ "${here}" == "${NODE}" ]] || fail "this bundle is for node ${NODE}, this is ${here:-an unnamed node}; nothing was changed"
else
  log "WARNING: ${ROOT}/etc/flatcar-builder/node.env is missing; cannot confirm this is node ${NODE}"
fi

if [[ "${PARTIAL}" == 1 ]]; then
  for app in ${APPS}; do
    [[ -f "${STATE}/apps/${app}.json" ]] \
      || fail "app ${app} is not installed on this node — a new app comes with a full app bundle; nothing was changed"
  done
fi

need_os=0
if [[ "${MODE}" == "os" ]]; then
  current="$(sed -n 's/^VERSION_ID=//p' "${ROOT}/etc/os-release" 2>/dev/null | tr -d '"' || true)"
  if [[ -n "${current}" && "${current}" == "${TARGET_FLATCAR_VERSION}" ]]; then
    log "Flatcar is already at ${current}; no OS update"
  else
    log "Flatcar ${current:-(unknown)} -> ${TARGET_FLATCAR_VERSION}"
    need_os=1
  fi
fi

if [[ "${DRY_RUN}" -eq 1 ]]; then
  log "DRY RUN: would disable automatic updates, run lib/update-app.sh$( [[ ${need_os} -eq 1 ]] && echo ' --no-up, lib/update-os.sh and reboot' )"
  exit 0
fi

# 2. Automatic updates off. Advisory: its exit code says whether something is
# still reachable, which is worth a line but not worth stopping the update for.
if bash "${BUNDLE_DIR}/lib/disable-auto-updates.sh" >/dev/null 2>&1; then
  log "no automatic OS update or update-triggered reboot is active"
else
  log "WARNING: an automatic update mechanism is still reachable; run lib/disable-auto-updates.sh --verify-only"
fi

# 3. Apps. With an OS update to follow, nothing is brought up: the reboot does
# that from the new files, and starting containers first is work undone seconds
# later.
up_args=()
[[ "${need_os}" -eq 1 ]] && up_args=(--no-up)
bash "${BUNDLE_DIR}/lib/update-app.sh" "${up_args[@]}" || fail "the app update failed (see above)"
mkdir -p "${STATE}"
cp "${BUNDLE_DIR}/MANIFEST.json" "${STATE}/last-bundle.json"

# 4. OS, then reboot into it.
if [[ "${need_os}" -eq 1 ]]; then
  bash "${BUNDLE_DIR}/lib/update-os.sh" || fail "the OS update failed; the node keeps running ${current:-its current version}"
  log "rebooting into ${TARGET_FLATCAR_VERSION}"
  sync
  systemctl reboot
fi

log "done"
