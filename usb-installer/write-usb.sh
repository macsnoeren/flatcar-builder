#!/bin/bash
#
# Write the Flatcar maintenance image to a USB stick and adapt it to the size of
# that stick.
#
# The image is built to fit its contents, so it is almost always smaller than
# the target device. Two things therefore happen after the copy:
#
#   1. The backup GPT is relocated to the last sectors of the device. Firmware
#      that finds a GPT whose backup header is missing or misplaced refuses to
#      boot the disk, which presents as "the stick is detected but nothing
#      happens".
#   2. The UPDATE partition is grown to fill the remaining space.
#
# Usage:
#
#     sudo ./write-usb.sh <image> <device> [--yes] [--force]
#
#     --yes     do not ask to type the device path
#     --force   also write to a disk that is not removable or on USB
#
# Example:
#
#     sudo ./write-usb.sh output/flatcar-maintenance-usb.img /dev/sdb
#

set -euo pipefail

UPDATE_PARTITION=4

fail()
{
    echo "ERROR: $*" >&2
    exit 1
}

usage()
{
    echo "Usage: $0 <image> <device> [--yes] [--force]" >&2
    exit 2
}

[ $# -ge 2 ] || usage

IMAGE="$1"
DEVICE="$2"
shift 2

ASSUME_YES=0
FORCE=0

for arg in "$@"; do
    case "$arg" in
        --yes)   ASSUME_YES=1 ;;
        --force) FORCE=1 ;;
        *)       usage ;;
    esac
done

# =============================================================================
# Checks
# =============================================================================

[ "$(id -u)" -eq 0 ] || fail "this script must be run as root"

[ -f "$IMAGE" ] || fail "image not found: $IMAGE"
[ -b "$DEVICE" ] || fail "not a block device: $DEVICE"

for tool in sgdisk partprobe resize2fs e2fsck blockdev lsblk; do
    command -v "$tool" >/dev/null || fail "required tool not installed: $tool"
done

if [[ "$DEVICE" =~ ^/dev/(sd|vd|hd)[a-z]+[0-9]+$ ]] ||
   [[ "$DEVICE" =~ ^/dev/(nvme[0-9]+n[0-9]+|mmcblk[0-9]+)p[0-9]+$ ]]; then
    fail "$DEVICE is a partition. Give the whole disk, for example /dev/sdb"
fi

IMAGE_BYTES="$(stat -c%s "$IMAGE")"
DEVICE_BYTES="$(blockdev --getsize64 "$DEVICE")"

if [ "$IMAGE_BYTES" -gt "$DEVICE_BYTES" ]; then
    fail "image is $((IMAGE_BYTES / 1048576)) MiB but $DEVICE is only $((DEVICE_BYTES / 1048576)) MiB"
fi

# lsblk follows the disk upwards, through LVM and LUKS. /proc/mounts lists those
# as /dev/mapper/..., so the disk of a workstation can look unused there.
MOUNTED="$(lsblk -nr -o MOUNTPOINT "$DEVICE" | sed '/^$/d')"

if [ -n "$MOUNTED" ]; then
    echo "Mounted from $DEVICE:"
    echo "$MOUNTED"
    fail "unmount them before writing"
fi

# Two calls, because an empty TRAN shifts the columns of a combined one.
TRAN="$(lsblk -dno TRAN "$DEVICE" | tr -d ' ')"
REMOVABLE="$(lsblk -dno RM "$DEVICE" | tr -d ' ')"

if [ "$TRAN" != "usb" ] && [ "$REMOVABLE" != "1" ] && [ "$FORCE" -ne 1 ]; then
    fail "$DEVICE is not removable or on USB. Pass --force if this is the right disk"
fi

# =============================================================================
# Confirmation
# =============================================================================

echo
echo "Target device:"
lsblk -o NAME,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINT "$DEVICE" | sed 's/^/  /'
echo
echo "Image:  $IMAGE ($((IMAGE_BYTES / 1048576)) MiB)"
echo "Device: $DEVICE ($((DEVICE_BYTES / 1048576)) MiB)"
echo
echo "EVERYTHING ON $DEVICE WILL BE DESTROYED."
echo

if [ "$ASSUME_YES" -ne 1 ]; then
    read -r -p "Type the device path to confirm: " CONFIRM
    [ "$CONFIRM" = "$DEVICE" ] || fail "confirmation did not match, nothing was written"
fi

# =============================================================================
# Write
# =============================================================================

echo
echo "Writing image..."

dd if="$IMAGE" of="$DEVICE" bs=16M status=progress conv=fsync

sync

# =============================================================================
# Relocate the backup GPT
# =============================================================================

echo
echo "Relocating the backup GPT to the end of the device..."

sgdisk --move-second-header "$DEVICE"

partprobe "$DEVICE"

# =============================================================================
# Grow the UPDATE partition
# =============================================================================

echo
echo "Growing the UPDATE partition..."

PART_START="$(sgdisk --info="$UPDATE_PARTITION" "$DEVICE" | awk '/First sector/ {print $3}')"
PART_NAME="$(sgdisk --info="$UPDATE_PARTITION" "$DEVICE" | sed -n "s/^Partition name: '\(.*\)'$/\1/p")"

[ -n "$PART_START" ] || fail "could not read the start sector of partition $UPDATE_PARTITION"

sgdisk \
    --delete="$UPDATE_PARTITION" \
    --new="${UPDATE_PARTITION}:${PART_START}:0" \
    --typecode="${UPDATE_PARTITION}:8300" \
    --change-name="${UPDATE_PARTITION}:${PART_NAME:-UPDATE}" \
    "$DEVICE" >/dev/null

partprobe "$DEVICE"
udevadm settle 2>/dev/null || sleep 2

# Partition node naming differs between /dev/sdb4 and /dev/nvme0n1p4.
if [ -b "${DEVICE}${UPDATE_PARTITION}" ]; then
    PART_DEV="${DEVICE}${UPDATE_PARTITION}"
elif [ -b "${DEVICE}p${UPDATE_PARTITION}" ]; then
    PART_DEV="${DEVICE}p${UPDATE_PARTITION}"
else
    fail "partition node for partition $UPDATE_PARTITION did not appear"
fi

e2fsck -fp "$PART_DEV" || true
resize2fs "$PART_DEV"

sync

# =============================================================================
# Result
# =============================================================================

echo
sgdisk --verify "$DEVICE"

echo
lsblk -o NAME,SIZE,LABEL,FSTYPE "$DEVICE"

echo
cat <<'EOF'
Done. The USB stick is ready, but it holds no build yet. To add one:

  sudo mount /dev/disk/by-label/FLATCAR-UPDATE /mnt
  sudo cp -a <deployment>/build/usb /mnt/<date>-<deployment>
  sudo umount /mnt

Use a new directory for every build. A stick can hold several.
EOF
