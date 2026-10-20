# Builder image for the IronBridge Flatcar maintenance USB.
#
# Build:
#     docker build -t flatcar-maintenance-builder .
#
# Run (privileged: the build creates loop devices and mounts filesystems):
#     docker run --rm --privileged \
#         -v "$PWD/output:/output" \
#         -v "$PWD/update:/build/update:ro" \
#         -v "$PWD/cache:/build/cache" \
#         flatcar-maintenance-builder
#
# The cache volume is optional. live-build caches the bootstrap stage and the
# downloaded packages there, which turns a repeated build from twenty minutes
# into a few.

FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /build

# -----------------------------------------------------------------------------
# Build dependencies
#
# qemu-system-x86 and ovmf are only used by the boot smoke test at the end of
# build-usb-image.sh. Set SMOKE_TEST=0 to skip it.
#
# grub-pc-bin rather than grub-pc: it carries grub-bios-setup as
# /usr/lib/grub/i386-pc/grub-bios-setup, which is not on PATH and which
# build-usb-image.sh therefore resolves by hand. The full grub-pc package ships
# the /usr/sbin wrapper but also runs debconf and wants to configure a real
# bootloader, which does not belong in a builder image.
# -----------------------------------------------------------------------------

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        live-build \
        debootstrap \
        squashfs-tools \
        xorriso \
        grub-pc-bin \
        grub-efi-amd64-bin \
        grub2-common \
        grub-common \
        mtools \
        dosfstools \
        e2fsprogs \
        gdisk \
        parted \
        util-linux \
        udev \
        rsync \
        curl \
        wget \
        ca-certificates \
        gzip \
        xz-utils \
        qemu-system-x86 \
        ovmf \
        && \
    rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Directory structure
# -----------------------------------------------------------------------------

RUN mkdir -p \
    /build/config/package-lists \
    /build/config/hooks/normal \
    /build/config/includes.chroot/usr/local/bin \
    /build/config/includes.chroot/etc/systemd/system/multi-user.target.wants \
    /build/update \
    /build/work \
    /build/cache \
    /output

# -----------------------------------------------------------------------------
# Debian live configuration
#
# --bootappend-live only affects the bootloader of the ISO that live-build
# generates, which this image discards. The kernel command line that actually
# gets used is written into /boot/grub/grub.cfg by build-usb-image.sh.
#
# non-free-firmware is in --archive-areas so that the firmware packages named
# in live/package-lists/ can be installed. A maintenance USB has to come up on
# server hardware whose network and storage controllers are not known in
# advance. The packages are listed individually rather than pulled in wholesale
# with --firmware-chroot, which would add well over a gigabyte of firmware for
# hardware this USB will never see.
# -----------------------------------------------------------------------------

RUN lb config \
    --distribution bookworm \
    --archive-areas "main contrib non-free-firmware" \
    --debian-installer false \
    --mirror-bootstrap "http://deb.debian.org/debian" \
    --mirror-chroot "http://deb.debian.org/debian" \
    --mirror-binary "http://deb.debian.org/debian"

# -----------------------------------------------------------------------------
# Live system contents
# -----------------------------------------------------------------------------

COPY live/package-lists/ /build/config/package-lists/
COPY live/hooks/ /build/config/hooks/normal/
COPY live/bin/flatcar-maintenance /build/config/includes.chroot/usr/local/bin/flatcar-maintenance
COPY live/systemd/flatcar-maintenance.service /build/config/includes.chroot/etc/systemd/system/flatcar-maintenance.service

RUN chmod +x \
        /build/config/includes.chroot/usr/local/bin/flatcar-maintenance \
        /build/config/hooks/normal/*.hook.chroot && \
    ln -sf \
        ../flatcar-maintenance.service \
        /build/config/includes.chroot/etc/systemd/system/multi-user.target.wants/flatcar-maintenance.service

# -----------------------------------------------------------------------------
# Build script
#
# The Flatcar update payload is not copied into the image. It is bind-mounted
# at /build/update when the container runs, so a several hundred megabyte
# payload never ends up in a Docker layer and can be replaced without a rebuild.
# -----------------------------------------------------------------------------

COPY build-usb-image.sh /build/build-usb-image.sh

RUN chmod +x /build/build-usb-image.sh

CMD ["/build/build-usb-image.sh"]
