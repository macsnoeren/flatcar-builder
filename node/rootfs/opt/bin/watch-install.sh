#!/usr/bin/env bash
#
# watch-install.sh — installs approved bundles on this node.
#
# Watches DEPLOY_DIR. When an archive '<name>.tar' has an approval
# '<name>.tar.approved' next to it, it:
#   1. waits DELAY seconds (default 30),
#   2. marks the archive as handled ('<name>.tar.installed'), so one approval
#      leads to exactly ONE attempt,
#   3. extracts it into WORK_DIR/<name> and runs INSTALL_SCRIPT from its root,
#   4. appends the outcome to the marker.
#
# The marker is written BEFORE the install runs: the install may reboot the
# node, and a marker written afterwards can lose that race and install the same
# bundle again on the way back. A bundle that cannot install at all is then
# reported once instead of every DELAY seconds. A failed attempt is not retried:
# fix the bundle and remove the marker, or upload it under another name.
#
# Who places the archive and the approval is not this script's business; on a
# node that is typically a container that verified the bundle's signature.
#
# Extractions are pruned to the KEEP_WORK_DIRS (default 3) most recent ones,
# before the next extraction and once at start; the last few are kept because
# after a failed install the extracted tree is what an operator looks at.
# KEEP_WORK_DIRS=0 disables the pruning.
#
# Logs go to stdout (the journal) and to LOG_FILE. Nothing goes over the network.
#
# WATCH_ONCE=1 makes one pass over DEPLOY_DIR and exits (for tests).
set -u

NODE_NAME="${NODE_NAME:-$(hostname)}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/deploy}"
WORK_DIR="${WORK_DIR:-${DEPLOY_DIR}/work}"
LOG_FILE="${LOG_FILE:-/var/log/watch-install.log}"
DELAY="${DELAY:-30}"
INSTALL_SCRIPT="${INSTALL_SCRIPT:-install.sh}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
KEEP_WORK_DIRS="${KEEP_WORK_DIRS:-3}"
WATCH_ONCE="${WATCH_ONCE:-0}"

log() {
    local line
    line="$(date -u +'%Y-%m-%dT%H:%M:%SZ') ${NODE_NAME} [watch-install] $*"
    echo "${line}"
    printf '%s\n' "${line}" >> "${LOG_FILE}" 2>/dev/null || true
}

# mark appends one dated line to an archive's marker. Its existence is what
# stops a second attempt; the lines are for the operator afterwards.
mark() {
    local marker="$1"; shift
    printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*" >> "${marker}"
}

prune_work_dirs() {
    case "${KEEP_WORK_DIRS}" in
        ''|*[!0-9]*) log "WARNING: KEEP_WORK_DIRS='${KEEP_WORK_DIRS}' is not a number -- not pruning ${WORK_DIR}"; return 0 ;;
        0) return 0 ;;
    esac
    local removed=0 dir
    while IFS= read -r dir; do
        [ -n "${dir}" ] || continue
        rm -rf "${dir}" && removed=$((removed + 1))
    done < <(find "${WORK_DIR}" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%p\n' 2>/dev/null \
                 | sort -rn | tail -n "+$((KEEP_WORK_DIRS + 1))" | cut -f2-)
    if [ "${removed}" -gt 0 ]; then
        log "cleaned up ${removed} old extracted bundle(s) from ${WORK_DIR} (keeping the ${KEEP_WORK_DIRS} most recent)"
    fi
    return 0
}

install_one() {
    local archive="$1" name approval marker dest rc
    name="$(basename "${archive}")"
    approval="${archive}.approved"
    marker="${archive}.installed"

    [ -e "${approval}" ] || return 0   # no approval yet
    [ -e "${marker}" ]   && return 0   # already handled

    log "'${name}' is present AND approved -> installing in ${DELAY}s"
    sleep "${DELAY}"

    # Claim the bundle before touching anything; see mark() above.
    mark "${marker}" "attempt started"

    log "starting installation of '${name}'"
    dest="${WORK_DIR}/${name%.tar}"
    rm -rf "${dest}"; mkdir -p "${dest}"

    # dest is now the newest, so it survives; pruning first frees the space of
    # the old extractions for this one.
    prune_work_dirs

    if ! tar -xf "${archive}" -C "${dest}"; then
        mark "${marker}" "FAILED: could not extract the archive"
        log "ERROR: extracting '${name}' failed -- not retrying; remove ${marker} to try again"
        return 0
    fi

    if [ ! -f "${dest}/${INSTALL_SCRIPT}" ]; then
        mark "${marker}" "FAILED: no ${INSTALL_SCRIPT} in the root of the archive"
        log "ERROR: no ${INSTALL_SCRIPT} in the root of '${name}' -- not retrying; remove ${marker} to try again"
        return 0
    fi

    log "running ${INSTALL_SCRIPT} from '${name}'"
    if ( cd "${dest}" && bash "${INSTALL_SCRIPT}" ); then
        mark "${marker}" "installed"
        log "installation of '${name}' SUCCEEDED"
    else
        rc=$?
        mark "${marker}" "FAILED: ${INSTALL_SCRIPT} exited ${rc}"
        log "ERROR: installation of '${name}' failed (exit ${rc}) -- not retrying; remove ${marker} to try again"
    fi
}

mkdir -p "${DEPLOY_DIR}" "${WORK_DIR}" "$(dirname "${LOG_FILE}")"
log "watching ${DEPLOY_DIR} (delay=${DELAY}s, install-script=${INSTALL_SCRIPT}, keep-work-dirs=${KEEP_WORK_DIRS})"
prune_work_dirs

while true; do
    shopt -s nullglob
    for archive in "${DEPLOY_DIR}"/*.tar; do
        install_one "${archive}"
    done
    shopt -u nullglob
    [ "${WATCH_ONCE}" = "1" ] && exit 0
    sleep "${POLL_INTERVAL}"
done
