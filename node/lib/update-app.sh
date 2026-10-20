#!/usr/bin/env bash
# update-app.sh — puts the apps of a bundle on this node and brings them up.
#
#   bash lib/update-app.sh [--no-up]
#
# Run by install.sh, from the extracted bundle. It never reboots. In order:
#
#   1. replace_dirs.list  emptied (a part or file dropped from node.yml goes)
#   2. directories.list   created, with their mode
#   3. rootfs/            copied over /
#   4. secrets/           copied over /, root:root 0600
#   5. images             copied to /opt/images and loaded
#   6. bin/docker-compose, units.list, the journal cap   (full bundles)
#   7. 'up -d'            only what changed is recreated (skipped with --no-up)
#   8. post-update hooks of the apps in the bundle
#
# A full bundle carries the whole tree; a bundle of selected apps carries only
# theirs, and steps 1 and 7 then touch only those apps.
#
# ROOT (default empty) prefixes every path on the node, for tests.
set -euo pipefail

BUNDLE_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )"
ROOT="${ROOT:-}"
DC="${DOCKER_COMPOSE:-${ROOT}/opt/bin/docker-compose}"

UP=1
for arg in "$@"; do
  case "${arg}" in
    --no-up) UP=0 ;;
    *) echo "usage: update-app.sh [--no-up]" >&2; exit 2 ;;
  esac
done

# shellcheck source=/dev/null
. "${BUNDLE_DIR}/bundle.env"

# Ownership is root's on a node; a test runs as an ordinary user.
OWN=()
[[ "${EUID}" -eq 0 ]] && OWN=(-o root -g root)

# A path given to rm -rf has to be at least two levels deep. The builder checks
# this too; this is the last line, on the machine where it would hurt.
deep_enough() { [[ "$1" == /?*/?* && "$1" != *..* ]]; }

# install_file <src> <dest> <mode>: written next to dest and renamed into place,
# so a running program (docker-compose, this watcher's own script) keeps its
# old inode instead of being overwritten under its feet.
install_file() {
  local src="$1" dest="$2" mode="$3"
  # A node that started a container before this file existed has a DIRECTORY
  # here: docker creates one for a missing bind-mount source. Copying onto it
  # would hide the file inside it.
  if [[ -d "${dest}" && ! -L "${dest}" ]]; then
    echo "  [!] ${dest#"${ROOT}"} is a directory (left by a bind mount); replacing it"
    rm -rf "${dest}"
  fi
  install "${OWN[@]}" -m "${mode}" "${src}" "${dest}.fcb-new"
  mv -f "${dest}.fcb-new" "${dest}"
}

# copy_tree <dir> <secret 0|1>
copy_tree() {
  local src="$1" secret="$2" rel dest parent mode
  [[ -d "${src}" ]] || return 0
  while IFS= read -r -d '' rel; do
    dest="${ROOT}/${rel}"
    parent="$(dirname "${dest}")"
    if [[ ! -d "${parent}" ]]; then
      # For a secret only the directory holding it becomes 0700; any parent
      # above it that is missing is an ordinary 0755 directory.
      # shellcheck disable=SC2174
      if [[ "${secret}" -eq 1 ]]; then mkdir -p -m 0700 "${parent}"; else mkdir -p "${parent}"; fi
    fi
    if [[ "${secret}" -eq 1 ]]; then
      mode=0600
      echo "  ${rel}"            # the name, never the contents: this goes to the log
    else
      mode="$(stat -c '%a' "${src}/${rel}")"
    fi
    install_file "${src}/${rel}" "${dest}" "${mode}"
  done < <(cd "${src}" && find . -type f -printf '%P\0' | sort -z)
}

echo "[+] App update for node ${NODE} from ${BUNDLE} ($( [[ "${PARTIAL}" == 1 ]] && echo "apps: ${APPS}" || echo "full" ))"

# 1. Directories replaced as a whole.
if [[ -s "${BUNDLE_DIR}/replace_dirs.list" ]]; then
  while IFS= read -r d; do
    [[ -n "${d}" ]] || continue
    deep_enough "${d}" || { echo "[x] refusing to replace ${d}: not deep enough to be safe" >&2; exit 1; }
    echo "[+] Replacing ${d}"
    rm -rf "${ROOT}${d}"
    mkdir -p "${ROOT}${d}"
  done < "${BUNDLE_DIR}/replace_dirs.list"
fi

# 2. Directories, with their mode — also the ones that already exist, so a mode
# changed in node.yml reaches the node.
if [[ -s "${BUNDLE_DIR}/directories.list" ]]; then
  echo "[+] Directories"
  while IFS=$'\t' read -r mode d; do
    [[ -n "${d}" ]] || continue
    mkdir -p "${ROOT}${d}"
    chmod "${mode}" "${ROOT}${d}"
    [[ "${#OWN[@]}" -gt 0 ]] && chown root:root "${ROOT}${d}"
  done < "${BUNDLE_DIR}/directories.list"
fi

# 3. Files.
echo "[+] Files"
copy_tree "${BUNDLE_DIR}/rootfs" 0

# 4. Secrets. Only names are printed.
if [[ -d "${BUNDLE_DIR}/secrets" ]] && [[ -n "$(ls -A "${BUNDLE_DIR}/secrets")" ]]; then
  echo "[+] Secrets"
  copy_tree "${BUNDLE_DIR}/secrets" 1
fi

# 5. Images. A full bundle names every image the node uses, so tars it does not
# name are left over from an earlier stack and go; import-images.service would
# otherwise load them at every boot.
mkdir -p "${ROOT}/opt/images"
if [[ "${PARTIAL}" != 1 ]]; then
  for f in "${ROOT}/opt/images"/*.tar; do
    [[ -e "${f}" ]] || continue
    if ! grep -qxF "$(basename "${f}")" "${BUNDLE_DIR}/images.list"; then
      echo "[+] Removing unused image tar $(basename "${f}")"
      rm -f "${f}"
    fi
  done
fi
if [[ -s "${BUNDLE_DIR}/images.list" ]]; then
  echo "[+] Images"
  while IFS= read -r tarname; do
    [[ -n "${tarname}" ]] || continue
    src="${BUNDLE_DIR}/images/${tarname}"
    [[ -f "${src}" ]] || { echo "[x] missing image tar: ${src}" >&2; exit 1; }
    echo "  ${tarname}"
    install_file "${src}" "${ROOT}/opt/images/${tarname}" 0644
    docker load -i "${ROOT}/opt/images/${tarname}" >/dev/null
  done < "${BUNDLE_DIR}/images.list"
fi

# 6. Platform pieces of a full bundle.
if [[ -f "${BUNDLE_DIR}/bin/docker-compose" ]]; then
  mkdir -p "${ROOT}/opt/bin"
  install_file "${BUNDLE_DIR}/bin/docker-compose" "${ROOT}/opt/bin/docker-compose" 0755
fi
if [[ -s "${BUNDLE_DIR}/units.list" ]]; then
  echo "[+] Units"
  systemctl daemon-reload
  while IFS= read -r unit; do
    [[ -n "${unit}" ]] || continue
    # Enabled, not restarted: restarting compose-stack would stop the stack, and
    # restarting watch-install would kill the install that is running now. A
    # changed unit takes effect at the next boot.
    systemctl enable "${unit}" >/dev/null 2>&1 || echo "  [!] could not enable ${unit}"
    echo "  ${unit}"
  done < "${BUNDLE_DIR}/units.list"
fi
JOURNALD_CONF="${BUNDLE_DIR}/rootfs/etc/systemd/journald.conf.d/10-size.conf"
if [[ -f "${JOURNALD_CONF}" ]]; then
  # The journal cap takes effect now: restarting journald disturbs no container,
  # and the vacuum trims what is already there.
  size="$(sed -n 's/^SystemMaxUse=//p' "${JOURNALD_CONF}")"
  systemctl restart systemd-journald || true
  [[ -n "${size}" ]] && { journalctl --vacuum-size="${size}" >/dev/null 2>&1 || true; }
  # The docker cap does NOT take effect now: daemon.json is read when the daemon
  # starts and a container's log settings are fixed when it is created.
  # Restarting the daemon stops every container, which is a reboot's work.
  echo "    the docker log cap applies to containers created from here on"
fi
if [[ "${PARTIAL}" != 1 ]]; then
  echo "    network configuration in /etc/systemd/network takes effect at the next boot"
fi

# 7. Up. No 'down' first: 'up -d' recreates only the services whose image or
# configuration changed, and leaves everything else running.
if [[ "${UP}" -eq 1 ]]; then
  set -a
  # shellcheck source=/dev/null
  . "${ROOT}/etc/flatcar-builder/node.env"
  set +a
  if [[ "${PARTIAL}" == 1 ]]; then
    services=()
    for app in ${APPS}; do
      while IFS= read -r s; do [[ -n "${s}" ]] && services+=("${s}"); done < "${BUNDLE_DIR}/apps/${app}/services"
    done
    if [[ "${#services[@]}" -gt 0 ]]; then
      echo "[+] Bringing up ${services[*]}"
      "${DC}" -f "${ROOT}/opt/compose/compose.yml" up -d --no-deps "${services[@]}"
    fi
  else
    echo "[+] Bringing up the stack (only what changed is recreated)"
    "${DC}" -f "${ROOT}/opt/compose/compose.yml" up -d --remove-orphans
  fi

  # 8. Hooks: reports, not rollbacks. A failing one is a warning; the rest of
  # the node is up.
  for app in ${APPS}; do
    hook="${BUNDLE_DIR}/apps/${app}/hooks/post-update.sh"
    [[ -f "${hook}" ]] || continue
    echo "[+] post-update hook of ${app}"
    if ! NODE_NAME="${NODE}" BUNDLE_DIR="${BUNDLE_DIR}" bash "${hook}"; then
      echo "[!] the post-update hook of ${app} failed (see above)"
    fi
  done
else
  echo "[+] Not bringing anything up (--no-up); that happens at the next boot"
fi

echo "[+] App update done for node ${NODE}"
