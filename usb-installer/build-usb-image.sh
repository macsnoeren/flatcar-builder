#!/bin/bash
#
# Build the IronBridge Flatcar maintenance USB image.
#
# Runs inside the builder container described by the Dockerfile next to this
# script. The container must be started with --privileged, because the build
# creates loop devices and mounts filesystems through the host kernel.
#
# The image is sized to its contents, not to the target USB stick. Partition 4
# (UPDATE) is grown to fill the stick afterwards by write-usb.sh. Writing an
# image that is larger than the stick leaves the backup GPT unwritten, and
# firmware then refuses to boot the disk.
#
# Environment variables:
#
#   EFI_SIZE_MB      size of the EFI system partition in MiB   (default 128)
#   LIVE_EXTRA_MB    headroom on top of the live files in MiB  (default 192)
#   UPDATE_SIZE_MB   size of the UPDATE partition in MiB       (default: content + 512, minimum 1024)
#   LIVE_LABEL       filesystem label of the LIVE partition    (default IB-LIVE)
#   UPDATE_LABEL     filesystem label of the UPDATE partition  (default IB-UPDATE)
#   EFI_LABEL        filesystem label of the EFI partition     (default IB-EFI)
#   SMOKE_TEST       run the QEMU boot test, 1 or 0            (default 1)
#

set -euo pipefail

# =============================================================================
# Configuration
# =============================================================================

OUTPUT="/output/flatcar-maintenance-usb.img"

EFI_SIZE_MB="${EFI_SIZE_MB:-128}"
LIVE_EXTRA_MB="${LIVE_EXTRA_MB:-192}"
UPDATE_SIZE_MB="${UPDATE_SIZE_MB:-}"

LIVE_LABEL="${LIVE_LABEL:-IB-LIVE}"
UPDATE_LABEL="${UPDATE_LABEL:-IB-UPDATE}"
EFI_LABEL="${EFI_LABEL:-IB-EFI}"

SMOKE_TEST="${SMOKE_TEST:-1}"

# Marker printed by GRUB before the menu is drawn. The smoke test greps the
# emulated serial console for it, which proves the whole bootloader chain.
GRUB_MARKER="IRONBRIDGE-GRUB-OK"

BUILD="/build"
WORK="$BUILD/work"
UPDATE_SRC="$BUILD/update"

MOUNT_EFI="$WORK/mnt-efi"
MOUNT_LIVE="$WORK/mnt-live"
MOUNT_UPDATE="$WORK/mnt-update"
ISO_MOUNT="$WORK/iso"

LOOP=""
LOOP_EFI=""
LOOP_LIVE=""
LOOP_UPDATE=""

SECTOR_SIZE=512
MIB_SECTORS=$((1024 * 1024 / SECTOR_SIZE))

# =============================================================================
# Helpers
# =============================================================================

step()
{
    echo
    echo "=============================================="
    echo "$*"
    echo "=============================================="
    echo
}

fail()
{
    echo "ERROR: $*" >&2
    exit 1
}

cleanup()
{
    set +e

    echo
    echo "Cleaning up..."

    sync

    mountpoint -q "$MOUNT_UPDATE" && umount "$MOUNT_UPDATE"
    mountpoint -q "$MOUNT_LIVE" && umount "$MOUNT_LIVE"
    mountpoint -q "$MOUNT_EFI" && umount "$MOUNT_EFI"
    mountpoint -q "$ISO_MOUNT" && umount "$ISO_MOUNT"

    [ -n "$LOOP_UPDATE" ] && losetup -d "$LOOP_UPDATE" 2>/dev/null
    [ -n "$LOOP_LIVE" ] && losetup -d "$LOOP_LIVE" 2>/dev/null
    [ -n "$LOOP_EFI" ] && losetup -d "$LOOP_EFI" 2>/dev/null
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null

    return 0
}

trap cleanup EXIT

# =============================================================================
# Prepare working directories and loop device nodes
# =============================================================================

mkdir -p "$WORK" "$MOUNT_EFI" "$MOUNT_LIVE" "$MOUNT_UPDATE" "$ISO_MOUNT" "$UPDATE_SRC"

echo "Preparing loop device nodes..."

if [ ! -e /dev/loop-control ]; then
    mknod /dev/loop-control c 10 237
    chmod 660 /dev/loop-control
fi

for i in $(seq 0 63); do
    if [ ! -e "/dev/loop${i}" ]; then
        mknod "/dev/loop${i}" b 7 "$i"
        chmod 660 "/dev/loop${i}"
    fi
done

# =============================================================================
# Preflight
#
# Everything the build needs is checked here, before lb build spends twenty
# minutes bootstrapping Debian. A tool that is missing at step 9 costs a whole
# build; the same tool missing here costs a second.
# =============================================================================

echo "Checking build tools..."

for tool in lb sgdisk losetup mount umount mountpoint truncate dd stat du \
            mkfs.vfat mkfs.ext4 sha256sum grub-mkimage grub-mkstandalone
do
    command -v "$tool" >/dev/null || fail "required tool not installed: $tool"
done

# On Debian, grub-pc-bin installs grub-bios-setup under /usr/lib/grub/i386-pc/
# and not on PATH. Only the full grub-pc package ships the /usr/sbin wrapper,
# and that package runs debconf and wants to configure a real bootloader, which
# does not belong in a builder container.
GRUB_BIOS_SETUP="$(command -v grub-bios-setup || true)"

if [ -z "$GRUB_BIOS_SETUP" ] && [ -x /usr/lib/grub/i386-pc/grub-bios-setup ]; then
    GRUB_BIOS_SETUP=/usr/lib/grub/i386-pc/grub-bios-setup
fi

[ -n "$GRUB_BIOS_SETUP" ] || fail "grub-bios-setup not found; install grub-pc-bin"

for dir in /usr/lib/grub/i386-pc /usr/lib/grub/x86_64-efi; do
    [ -d "$dir" ] || fail "GRUB platform directory missing: $dir"
done

[ -f /usr/lib/grub/i386-pc/boot.img ] || fail "/usr/lib/grub/i386-pc/boot.img missing"

echo "  grub-bios-setup: $GRUB_BIOS_SETUP"
echo "  all build tools present."

# =============================================================================
# 1. Build the Debian live system
# =============================================================================

step "1. Building the Debian live system"

cd "$BUILD"

lb build

# live-build leaves the kernel, initrd and squashfs in binary/live/ before it
# packs them into an ISO. Reading them from there avoids an ISO round trip and
# one loop mount. Older live-build versions name the kernel differently, so
# fall back to the ISO when the expected files are not all present.

LIVE_SRC="$BUILD/binary/live"

# sed -n 1p rather than head -1: head closes the pipe early, which under
# "set -o pipefail" turns a successful find into a failed command substitution.
resolve_live_file()
{
    find "$1" -maxdepth 1 -type f -name "$2" 2>/dev/null | sort | sed -n 1p
}

KERNEL="$(resolve_live_file "$LIVE_SRC" 'vmlinuz*')"
INITRD="$(resolve_live_file "$LIVE_SRC" 'initrd*')"
SQUASHFS="$(resolve_live_file "$LIVE_SRC" 'filesystem.squashfs')"

if [ -z "$KERNEL" ] || [ -z "$INITRD" ] || [ -z "$SQUASHFS" ]; then

    echo "binary/live/ is incomplete, falling back to the generated ISO..."

    ISO="$(find "$BUILD" -maxdepth 1 -type f -name '*.iso' | sort | sed -n 1p)"

    [ -n "$ISO" ] || fail "neither binary/live/ nor an ISO was produced by lb build"

    mount -o loop,ro "$ISO" "$ISO_MOUNT"

    LIVE_SRC="$ISO_MOUNT/live"

    KERNEL="$(resolve_live_file "$LIVE_SRC" 'vmlinuz*')"
    INITRD="$(resolve_live_file "$LIVE_SRC" 'initrd*')"
    SQUASHFS="$(resolve_live_file "$LIVE_SRC" 'filesystem.squashfs')"
fi

[ -n "$KERNEL" ] || fail "no kernel found in $LIVE_SRC"
[ -n "$INITRD" ] || fail "no initrd found in $LIVE_SRC"
[ -n "$SQUASHFS" ] || fail "no filesystem.squashfs found in $LIVE_SRC"

echo
echo "Live system files:"
ls -lh "$KERNEL" "$INITRD" "$SQUASHFS"

# =============================================================================
# 2. Calculate the partition layout
# =============================================================================

step "2. Calculating the partition layout"

LIVE_BYTES=$((
    $(stat -c%s "$KERNEL") +
    $(stat -c%s "$INITRD") +
    $(stat -c%s "$SQUASHFS")
))

LIVE_SIZE_MB=$(((LIVE_BYTES / 1048576) * 110 / 100 + LIVE_EXTRA_MB))

if [ -z "$UPDATE_SIZE_MB" ]; then

    UPDATE_CONTENT_MB=$(($(du -sk "$UPDATE_SRC" | cut -f1) / 1024))
    UPDATE_SIZE_MB=$((UPDATE_CONTENT_MB + 512))

    if [ "$UPDATE_SIZE_MB" -lt 1024 ]; then
        UPDATE_SIZE_MB=1024
    fi
fi

BIOS_START=$MIB_SECTORS
BIOS_END=$((BIOS_START + MIB_SECTORS - 1))

EFI_START=$((BIOS_END + 1))
EFI_SECTORS=$((EFI_SIZE_MB * MIB_SECTORS))
EFI_END=$((EFI_START + EFI_SECTORS - 1))

LIVE_START=$((EFI_END + 1))
LIVE_SECTORS=$((LIVE_SIZE_MB * MIB_SECTORS))
LIVE_END=$((LIVE_START + LIVE_SECTORS - 1))

UPDATE_START=$((LIVE_END + 1))
UPDATE_SECTORS=$((UPDATE_SIZE_MB * MIB_SECTORS))
UPDATE_END=$((UPDATE_START + UPDATE_SECTORS - 1))

# One extra MiB at the end holds the backup GPT.
IMAGE_SECTORS=$((UPDATE_END + 1 + MIB_SECTORS))
IMAGE_BYTES=$((IMAGE_SECTORS * SECTOR_SIZE))

echo "Partition sizes:"
printf '  BIOS-BOOT  %6s MiB   sectors %10d - %10d\n' 1 "$BIOS_START" "$BIOS_END"
printf '  EFI        %6s MiB   sectors %10d - %10d\n' "$EFI_SIZE_MB" "$EFI_START" "$EFI_END"
printf '  LIVE       %6s MiB   sectors %10d - %10d\n' "$LIVE_SIZE_MB" "$LIVE_START" "$LIVE_END"
printf '  UPDATE     %6s MiB   sectors %10d - %10d\n' "$UPDATE_SIZE_MB" "$UPDATE_START" "$UPDATE_END"
echo
printf '  image      %6s MiB   (%d bytes)\n' "$((IMAGE_BYTES / 1048576))" "$IMAGE_BYTES"

# =============================================================================
# 3. Create the image and its GPT
# =============================================================================

step "3. Creating the disk image"

rm -f "$OUTPUT"
truncate -s "$IMAGE_BYTES" "$OUTPUT"

sgdisk --zap-all "$OUTPUT" >/dev/null

sgdisk \
    --new=1:${BIOS_START}:${BIOS_END} \
    --typecode=1:ef02 \
    --change-name=1:BIOS-BOOT \
    --new=2:${EFI_START}:${EFI_END} \
    --typecode=2:ef00 \
    --change-name=2:EFI \
    --new=3:${LIVE_START}:${LIVE_END} \
    --typecode=3:8300 \
    --change-name=3:LIVE \
    --new=4:${UPDATE_START}:${UPDATE_END} \
    --typecode=4:8300 \
    --change-name=4:UPDATE \
    "$OUTPUT" >/dev/null

sgdisk --verify "$OUTPUT"

# =============================================================================
# 4. Attach loop devices
#
# The whole image is attached once, for grub-bios-setup, which needs to read
# the GPT. Each filesystem is attached separately with an explicit offset,
# because Docker does not reliably create /dev/loopXpN partition nodes.
# =============================================================================

step "4. Attaching loop devices"

LOOP="$(losetup --find --show "$OUTPUT")"

LOOP_EFI="$(losetup --find --show \
    --offset $((EFI_START * SECTOR_SIZE)) \
    --sizelimit $((EFI_SECTORS * SECTOR_SIZE)) "$OUTPUT")"

LOOP_LIVE="$(losetup --find --show \
    --offset $((LIVE_START * SECTOR_SIZE)) \
    --sizelimit $((LIVE_SECTORS * SECTOR_SIZE)) "$OUTPUT")"

LOOP_UPDATE="$(losetup --find --show \
    --offset $((UPDATE_START * SECTOR_SIZE)) \
    --sizelimit $((UPDATE_SECTORS * SECTOR_SIZE)) "$OUTPUT")"

echo "  image  = $LOOP"
echo "  EFI    = $LOOP_EFI"
echo "  LIVE   = $LOOP_LIVE"
echo "  UPDATE = $LOOP_UPDATE"

# =============================================================================
# 5. Create filesystems
#
# metadata_csum_seed and orphan_file are disabled explicitly. GRUB 2.06 cannot
# read an ext4 filesystem that uses them, and the resulting failure looks like
# "unknown filesystem" at boot. Debian bookworm's mke2fs happens to disable
# both by default, but the base image is not pinned to a Debian release.
# =============================================================================

step "5. Creating filesystems"

EXT4_OPTS="^metadata_csum_seed,^orphan_file"

mkfs.vfat -F32 -n "$EFI_LABEL" "$LOOP_EFI"
mkfs.ext4 -q -F -O "$EXT4_OPTS" -L "$LIVE_LABEL" "$LOOP_LIVE"
mkfs.ext4 -q -F -O "$EXT4_OPTS" -L "$UPDATE_LABEL" "$LOOP_UPDATE"

mount "$LOOP_EFI" "$MOUNT_EFI"
mount "$LOOP_LIVE" "$MOUNT_LIVE"
mount "$LOOP_UPDATE" "$MOUNT_UPDATE"

# =============================================================================
# 6. Install the live system
# =============================================================================

step "6. Installing the live system"

mkdir -p "$MOUNT_LIVE/live"

echo "Copying kernel..."
cp "$KERNEL" "$MOUNT_LIVE/live/vmlinuz"

echo "Copying initrd..."
cp "$INITRD" "$MOUNT_LIVE/live/initrd.img"

echo "Copying squashfs..."
cp "$SQUASHFS" "$MOUNT_LIVE/live/filesystem.squashfs"

if mountpoint -q "$ISO_MOUNT"; then
    umount "$ISO_MOUNT"
fi

sync

echo
df -h "$MOUNT_LIVE" | tail -1

# =============================================================================
# 7. GRUB configuration
# =============================================================================

step "7. Configuring GRUB"

mkdir -p "$MOUNT_LIVE/boot/grub"

cat > "$MOUNT_LIVE/boot/grub/grub.cfg" <<EOF
set timeout=5
set default=0

insmod part_gpt
insmod ext2
insmod search
insmod search_label

# Without the video modules the linux loader cannot set any mode at all, not
# even a text one, and reports "No suitable video mode found. Booting in blind
# mode." all_video pulls in vbe and vga on BIOS and efi_gop and efi_uga on UEFI.
insmod all_video

# UEFI has no VGA text mode to fall back to, so the firmware framebuffer is kept
# and handed to the kernel. On BIOS a plain text console is the more reliable
# choice on unknown server hardware.
if [ "\$grub_platform" = "efi" ]; then
    set gfxpayload=keep
else
    set gfxpayload=text
fi

# A serial console is useful on headless server hardware, and the build-time
# smoke test reads the marker below from the emulated serial port. Machines
# without a serial port simply keep the video console.
if serial --unit=0 --speed=115200; then
    terminal_input --append serial
    terminal_output --append serial
fi

echo "$GRUB_MARKER"

search --no-floppy --label $LIVE_LABEL --set=root

menuentry "IronBridge Flatcar Maintenance" {
    linux /live/vmlinuz boot=live components console=ttyS0,115200n8 console=tty0
    initrd /live/initrd.img
}

menuentry "IronBridge Flatcar Maintenance (safe graphics)" {
    set gfxpayload=text
    linux /live/vmlinuz boot=live components nomodeset console=tty0
    initrd /live/initrd.img
}

menuentry "IronBridge Flatcar Maintenance (verbose boot)" {
    linux /live/vmlinuz boot=live components debug console=ttyS0,115200n8 console=tty0
    initrd /live/initrd.img
}
EOF

# The early configuration is embedded in both bootloader images. It locates the
# LIVE partition by filesystem label and hands over to the configuration above.
# Nothing in it depends on how the image happened to be attached at build time,
# which is what made the previous grub-install approach unreliable: grub-install
# probed a bare loop device that had no partition table and no relationship to
# the disk it was installing to.

cat > "$WORK/early-grub.cfg" <<EOF
search --no-floppy --label $LIVE_LABEL --set=root
set prefix=(\$root)/boot/grub
configfile (\$root)/boot/grub/grub.cfg
EOF

# GRUB modules on the LIVE partition, for anything the core images do not carry.
mkdir -p "$MOUNT_LIVE/boot/grub/i386-pc" "$MOUNT_LIVE/boot/grub/x86_64-efi"
cp -a /usr/lib/grub/i386-pc/. "$MOUNT_LIVE/boot/grub/i386-pc/"
cp -a /usr/lib/grub/x86_64-efi/. "$MOUNT_LIVE/boot/grub/x86_64-efi/"

# =============================================================================
# 8. Install the UEFI bootloader
# =============================================================================

step "8. Installing the UEFI bootloader"

mkdir -p "$MOUNT_EFI/EFI/BOOT"

grub-mkstandalone \
    --format=x86_64-efi \
    --output="$MOUNT_EFI/EFI/BOOT/BOOTX64.EFI" \
    --modules="part_gpt ext2 fat search search_label configfile normal linux echo test serial terminal all_video" \
    --locales="" \
    --fonts="" \
    --themes="" \
    "boot/grub/grub.cfg=$WORK/early-grub.cfg"

[ -s "$MOUNT_EFI/EFI/BOOT/BOOTX64.EFI" ] || fail "BOOTX64.EFI was not created"

ls -lh "$MOUNT_EFI/EFI/BOOT/BOOTX64.EFI"

# =============================================================================
# 9. Install the BIOS bootloader
#
# grub-bios-setup reads the GPT on the image loop device itself and embeds
# core.img in the BIOS boot partition. It never has to work out which partition
# the /boot directory lives on, so the offset-based loop devices are invisible
# to it.
# =============================================================================

step "9. Installing the BIOS bootloader"

# boot.img and core.img are staged on the LIVE partition rather than in $WORK.
# grub-bios-setup resolves the root device of this directory through
# /proc/self/mountinfo, and for any path on Docker's overlayfs that lookup
# yields the literal string "overlay", which is not a path:
#
#     error: failed to get canonical path of `overlay'
#
# The LIVE partition is mounted from a loop device, so the lookup succeeds.
# /boot/grub/i386-pc/ is also where core.img conventionally lives.
BIOS_STAGE="$MOUNT_LIVE/boot/grub/i386-pc"
mkdir -p "$BIOS_STAGE"

grub-mkimage \
    --format=i386-pc \
    --output="$BIOS_STAGE/core.img" \
    --config="$WORK/early-grub.cfg" \
    --prefix="/boot/grub" \
    biosdisk part_gpt ext2 fat search search_label configfile normal linux echo test serial terminal all_video

cp /usr/lib/grub/i386-pc/boot.img "$BIOS_STAGE/boot.img"

CORE_BYTES="$(stat -c%s "$BIOS_STAGE/core.img")"

if [ "$CORE_BYTES" -gt $((MIB_SECTORS * SECTOR_SIZE)) ]; then
    fail "core.img is $CORE_BYTES bytes and does not fit the 1 MiB BIOS boot partition"
fi

sync

# GRUB_BIOS_SETUP is resolved in the preflight section.
#
# grub-bios-setup must be pointed at a block device. Given a plain file it
# resolves the host filesystem the file happens to sit on instead, and installs
# nothing useful. $LOOP is the whole image, so it reads the GPT correctly.
#
# It runs from the LIVE mount for the same reason the staging directory is
# there: the working directory is otherwise /build, on the overlay filesystem,
# which is another path GRUB may try to resolve to a device.
(
    cd "$MOUNT_LIVE"

    "$GRUB_BIOS_SETUP" \
        --directory="$BIOS_STAGE" \
        --device-map=/dev/null \
        --skip-fs-probe \
        "$LOOP"
)

sync

# Confirm that boot.img actually landed in the MBR and core.img in the BIOS
# boot partition. grub-bios-setup warns rather than fails in several cases.
# Read the MBR into a variable rather than piping into "grep -q": grep exits as
# soon as it matches, dd then takes SIGPIPE, and pipefail reports the whole
# pipeline as failed even though the match succeeded.
MBR_TEXT="$(dd if="$OUTPUT" bs=512 count=1 2>/dev/null | tr -dc '[:print:]')"

case "$MBR_TEXT" in
    *GRUB*) ;;
    *) fail "the MBR does not contain GRUB boot code" ;;
esac

if [ "$(dd if="$OUTPUT" bs=512 skip="$BIOS_START" count=8 2>/dev/null | tr -d '\0' | wc -c)" -eq 0 ]; then
    fail "the BIOS boot partition is all zeroes, core.img was not embedded"
fi

echo "core.img: $CORE_BYTES bytes, embedded in the BIOS boot partition."

# =============================================================================
# 10. Install the Flatcar update payload
# =============================================================================

step "10. Installing the Flatcar update payload"

# README.md documents the directory for whoever builds the image and is not part
# of the payload, so it is excluded and does not count towards "is there a
# payload at all".
PAYLOAD_COUNT="$(find "$UPDATE_SRC" -mindepth 1 -maxdepth 1 -not -name 'README.md' -printf 'x' | wc -c)"

if [ "$PAYLOAD_COUNT" -gt 0 ]; then

    find "$UPDATE_SRC" -mindepth 1 -maxdepth 1 -not -name 'README.md' \
        -exec cp -a {} "$MOUNT_UPDATE/" \;

    find "$MOUNT_UPDATE" -maxdepth 1 -name '*.sh' -exec chmod +x {} +

    if [ -f "$MOUNT_UPDATE/flatcar_production_update.gz" ]; then
        echo "Creating SHA256SUMS..."
        ( cd "$MOUNT_UPDATE" && sha256sum flatcar_production_update.gz > SHA256SUMS )
    fi

else
    echo "No update payload present. Mount one at $UPDATE_SRC to include it,"
    echo "or copy files onto the UPDATE partition after writing the USB."
fi

sync

# =============================================================================
# 11. Verification
# =============================================================================

step "11. Verifying the image contents"

echo "EFI:"
find "$MOUNT_EFI" -type f -printf '  %P (%s bytes)\n'

echo
echo "LIVE:"
find "$MOUNT_LIVE/live" -maxdepth 1 -type f -printf '  %f (%s bytes)\n'

echo
echo "GRUB:"
echo "  boot/grub/grub.cfg"
echo "  boot/grub/i386-pc/    ($(find "$MOUNT_LIVE/boot/grub/i386-pc" -name '*.mod' | wc -l) modules)"
echo "  boot/grub/x86_64-efi/ ($(find "$MOUNT_LIVE/boot/grub/x86_64-efi" -name '*.mod' | wc -l) modules)"

echo
echo "UPDATE:"
find "$MOUNT_UPDATE" -maxdepth 2 -type f -printf '  %P\n' | sort

# Release everything before the image is booted by QEMU.
umount "$MOUNT_UPDATE" "$MOUNT_LIVE" "$MOUNT_EFI"
losetup -d "$LOOP_UPDATE"; LOOP_UPDATE=""
losetup -d "$LOOP_LIVE"; LOOP_LIVE=""
losetup -d "$LOOP_EFI"; LOOP_EFI=""
losetup -d "$LOOP"; LOOP=""

sync

echo
sgdisk --print "$OUTPUT"

# =============================================================================
# 12. Boot smoke test
#
# Boots the finished image under QEMU in both firmware modes and checks that
# GRUB reaches its configuration. This catches a broken bootloader chain here
# instead of on the target hardware.
# =============================================================================

if [ "$SMOKE_TEST" = "1" ] && command -v qemu-system-x86_64 >/dev/null; then

    step "12. Boot smoke test"

    OVMF=""
    for candidate in \
        /usr/share/ovmf/OVMF.fd \
        /usr/share/OVMF/OVMF_CODE.fd \
        /usr/share/qemu/OVMF.fd
    do
        if [ -f "$candidate" ]; then
            OVMF="$candidate"
            break
        fi
    done

    # QEMU is stopped as soon as the marker appears, so a passing test costs a
    # few seconds rather than the whole timeout.
    smoke_test()
    {
        local name="$1"; shift
        local log="$WORK/smoke-${name}.log"
        local pid deadline

        : > "$log"

        qemu-system-x86_64 \
            -m 1024 \
            -display none \
            -serial "file:$log" \
            -drive "format=raw,file=$OUTPUT,if=ide" \
            "$@" >/dev/null 2>&1 &

        pid=$!
        deadline=$((SECONDS + 90))

        while [ "$SECONDS" -lt "$deadline" ]; do

            if grep -q "$GRUB_MARKER" "$log" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                wait "$pid" 2>/dev/null || true
                echo "  $name: GRUB reached its configuration."
                return 0
            fi

            kill -0 "$pid" 2>/dev/null || break

            sleep 2
        done

        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true

        echo "  $name: FAILED - GRUB never reached its configuration."
        echo "  ---- serial output ----"
        head -40 "$log" 2>/dev/null | sed 's/^/  /'
        echo "  -----------------------"
        return 1
    }

    SMOKE_FAILED=0

    smoke_test "BIOS" || SMOKE_FAILED=1

    if [ -n "$OVMF" ]; then
        smoke_test "UEFI" -bios "$OVMF" || SMOKE_FAILED=1
    else
        echo "  UEFI: skipped, no OVMF firmware found in the builder image."
    fi

    if [ "$SMOKE_FAILED" -ne 0 ]; then
        fail "the boot smoke test failed. Set SMOKE_TEST=0 to build anyway."
    fi

else
    step "12. Boot smoke test skipped"
fi

# =============================================================================
# Done
# =============================================================================

step "Build complete"

ls -lh "$OUTPUT"

echo
echo "Write it to a USB stick with:"
echo
echo "    sudo ./write-usb.sh output/$(basename "$OUTPUT") /dev/sdX"
echo
