#!/usr/bin/env bash
# Switch off every automatic Flatcar OS update and update-triggered reboot,
# and report what is left.
#
# A node built by Flatcar Builder never updates itself: the OS changes only when
# a bundle says so (install.sh -> lib/update-os.sh). Ignition applies exactly
# these settings at first boot; install.sh runs this in every bundle so a node
# converges even if something switched one back on. It is idempotent and
# touches nothing outside the update machinery.
#
# ROOT (default empty) prefixes the configuration path, for tests.
#
#   sudo bash disable-auto-updates.sh              apply, then verify
#   sudo bash disable-auto-updates.sh --verify-only  report only, change nothing
#
# Exit code 0 means no automatic update or update-triggered reboot mechanism
# is active.
set -euo pipefail

VERIFY_ONLY=0
if [[ "${1:-}" == "--verify-only" ]]; then VERIFY_ONLY=1; fi

# Flatcar carries two independent update paths and both are switched off:
#   update-engine        downloads a payload onto the inactive partition
#   locksmithd           reboots the node into it
#   systemd-sysupdate    the same for the newer sysext-based path; its timer is
#   systemd-sysupdate-reboot   enabled by default from Flatcar 4515 onwards
# Nothing else is in this list, and nothing else is modified.
UNITS=(
  update-engine.service
  locksmithd.service
  systemd-sysupdate.service
  systemd-sysupdate.timer
  systemd-sysupdate-reboot.service
  systemd-sysupdate-reboot.timer
)

CONF="${ROOT:-}/etc/flatcar/update.conf"

if [[ "${VERIFY_ONLY}" -eq 0 ]]; then
  [[ "${EUID}" -eq 0 || -n "${ROOT:-}" ]] || { echo "Run as root: sudo bash $0" >&2; exit 2; }

  # SERVER=disabled is the value update-engine reads as "no update server".
  # flatcar-update strips the line while it applies an approved payload and
  # restores it afterwards, so the deliberate update path keeps working.
  mkdir -p "$(dirname "${CONF}")"
  touch "${CONF}"
  sed -i '/^SERVER=/d; /^REBOOT_STRATEGY=/d' "${CONF}"
  printf 'SERVER=disabled\nREBOOT_STRATEGY=off\n' >> "${CONF}"
  echo "[+] ${CONF}: SERVER=disabled, REBOOT_STRATEGY=off"

  # Masking is the only reliable switch here: these units are enabled from
  # /usr, where systemctl disable cannot remove the enablement symlink. A mask
  # is a symlink to /dev/null under /etc/systemd/system and survives a reboot.
  # Masking a unit the image does not carry is harmless.
  for unit in "${UNITS[@]}"; do
    systemctl stop "${unit}" >/dev/null 2>&1 || true
    systemctl disable "${unit}" >/dev/null 2>&1 || true
    systemctl mask "${unit}" >/dev/null 2>&1 || true
    systemctl reset-failed "${unit}" >/dev/null 2>&1 || true
    echo "[+] masked ${unit}"
  done
  systemctl daemon-reload
fi

echo
echo "[=] State of the update units"
FAIL=0
for unit in "${UNITS[@]}"; do
  enabled="$(systemctl is-enabled "${unit}" 2>/dev/null || true)"
  active="$(systemctl is-active "${unit}" 2>/dev/null || true)"
  printf '    %-32s enabled=%-9s active=%s\n' \
    "${unit}" "${enabled:-absent}" "${active:-absent}"
  # An empty state means the image does not carry the unit at all, which is
  # as good as masked: nothing can start it.
  if [[ -n "${enabled}" && "${enabled}" != "masked" ]]; then FAIL=1; fi
  if [[ "${active}" == "active" || "${active}" == "activating" ]]; then FAIL=1; fi
done

# Anything else in the image that could fetch an OS update or reboot into one.
echo
echo "[=] Other update units on this node"
SCAN="$(systemctl list-unit-files --no-legend --no-pager \
          'systemd-sysupdate*' 'update-engine*' 'locksmith*' 'flatcar-update*' \
          2>/dev/null || true)"
if [[ -n "${SCAN}" ]]; then
  printf '%s\n' "${SCAN}" | awk '{ printf "    %-32s %s\n", $1, $2 }'
  # A static unit cannot start on its own; an enabled one can.
  STILL="$(printf '%s\n' "${SCAN}" | awk '$2 ~ /^enabled/ { print $1 }')"
  if [[ -n "${STILL}" ]]; then
    printf '%s\n' "${STILL}" | sed 's/^/    STILL ENABLED /'
    FAIL=1
  fi
else
  echo "    none"
fi

# Timers are the only thing that can start an update without an operator.
echo
echo "[=] Active timers"
systemctl list-timers --no-legend --no-pager 2>/dev/null | sed 's/^/    /' || true

echo
if [[ "${FAIL}" -eq 0 ]]; then
  echo "[+] No automatic OS update or update-triggered reboot is active."
else
  echo "[!] An automatic update mechanism is still reachable — see the lines above." >&2
fi
exit "${FAIL}"
