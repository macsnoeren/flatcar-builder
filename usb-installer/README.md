# usb-installer — maintenance USB for Flatcar nodes

Builds a bootable USB image that brings up a small Debian live system on a
machine and hands the operator a root shell with the USB builds of
[Flatcar Builder](../README.md) already mounted. From that shell a node is
installed with `scripts/install-flatcar.sh`.

The USB is a maintenance tool, not part of a running deployment. It is for the
first installation of an empty machine (or a reinstall that wipes the disk); an
installed node is updated with an os or app bundle placed in `/opt/deploy`,
never from this USB.

## What is on the image

The image is a GPT disk image with four partitions. It is not an ISO written to
a stick.

| # | Name | Type | Filesystem | Label | Contents |
|---|------|------|------------|-------|----------|
| 1 | `BIOS-BOOT` | `EF02` | none | — | embedded GRUB `core.img` for legacy BIOS boot |
| 2 | `EFI` | `EF00` | FAT32 | `IB-EFI` | `EFI/BOOT/BOOTX64.EFI` for UEFI boot |
| 3 | `LIVE` | `8300` | ext4 | `IB-LIVE` | kernel, initrd, squashfs and the GRUB configuration |
| 4 | `UPDATE` | `8300` | ext4 | `IB-UPDATE` | one or more USB builds (`build-flatcar.sh --mode usb`) |

Partitions 1 and 2 make the same stick bootable on both legacy BIOS and UEFI
firmware. Partition 4 is grown to fill the stick when the image is written, so
builds can also be added after the fact.

## Building

The build runs in a container so that the result does not depend on the
distribution of the workstation. The container must be privileged: it creates
loop devices and mounts filesystems through the host kernel.

```sh
cd usb-installer

docker build -t flatcar-maintenance-builder .

mkdir -p output cache

docker run --rm --privileged \
    -v "$PWD/output:/output" \
    -v "$PWD/update:/build/update:ro" \
    -v "$PWD/cache:/build/cache" \
    flatcar-maintenance-builder
```

The result is `output/flatcar-maintenance-usb.img`. A full build takes roughly
fifteen to thirty minutes, most of it spent bootstrapping Debian.

The cache volume is optional. live-build stores the bootstrap stage and the
downloaded packages there, which shortens a repeated build considerably. Delete
the directory to force a clean build.

The build checks that every tool it needs is present before it starts, so a
missing dependency fails within a second rather than after the bootstrap.

The image is sized to its contents, typically around 2 GiB, and deliberately
not to the target stick. An image larger than the stick cannot be written
completely; the backup GPT is then missing and firmware refuses to boot the
disk.

### Build parameters

Each is passed with `docker run -e NAME=value`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `EFI_SIZE_MB` | `128` | size of the EFI system partition in MiB |
| `LIVE_EXTRA_MB` | `192` | headroom added to the measured size of the live system |
| `UPDATE_SIZE_MB` | content + 512, minimum 1024 | fixed size of the UPDATE partition in MiB |
| `LIVE_LABEL` | `IB-LIVE` | filesystem label of the LIVE partition |
| `UPDATE_LABEL` | `IB-UPDATE` | filesystem label of the UPDATE partition |
| `EFI_LABEL` | `IB-EFI` | filesystem label of the EFI partition |
| `SMOKE_TEST` | `1` | run the QEMU boot test; `0` skips it |

### The boot smoke test

The last build step boots the finished image under QEMU, once with a legacy
BIOS and once with UEFI firmware, and checks that GRUB reaches its
configuration on the LIVE partition. Reaching that point proves the whole
bootloader chain: the embedded early configuration, the search by filesystem
label, the ext4 driver and the GRUB configuration itself.

A failing smoke test fails the build and prints the emulated serial output. Set
`SMOKE_TEST=0` to build an image regardless, for example to inspect it by hand.

The test covers the bootloader chain and nothing beyond it. It stops as soon as
GRUB prints its marker, so it says nothing about the kernel, the live system or
the maintenance shell. A build that passes the smoke test can still fail after
GRUB hands over.

## Writing the USB

[`write-usb.sh`](write-usb.sh) copies the image and then adapts it to the size
of the stick. It must run as root, and it destroys everything on the target
device.

```sh
sudo ./write-usb.sh output/flatcar-maintenance-usb.img /dev/sdX
```

The script refuses a partition (`/dev/sdb1`) instead of a whole disk, refuses a
device that is too small, refuses a device with mounted partitions, and asks for
the device path to be typed back before it writes anything. Pass `--yes` as a
third argument to skip the confirmation in automated use.

After the copy it does two things that a plain `dd` does not:

1. It moves the backup GPT to the last sectors of the device. Firmware that
   finds a GPT whose backup header is missing or misplaced rejects the disk, and
   the failure presents as a stick that is detected but never boots.
2. It grows the UPDATE partition and its filesystem to fill the remaining space.

## The USB builds

The UPDATE partition carries the USB builds produced by
`build-flatcar.sh <deployment> --mode usb`, which writes one to
`<deployment>/build/usb/`. A build holds the Flatcar image and installer, the
docker-compose binary, the docker image tars of all its nodes, and per node an
Ignition configuration, a `node.env` and the list of images that node needs:

```
build/usb/
├── flatcar/            flatcar_production_image.bin.bz2, flatcar-install
├── bin/                docker-compose
├── docker-images/      image tars, shared by all nodes of the build
├── nodes/<node>/       config.ign, config.bu, node.env, images.list
├── scripts/            install-flatcar.sh
├── README.txt          builder version, Flatcar version, nodes
└── checksums.sha256
```

A build carries no secrets; a node gets those with its first app bundle.

`build/usb/` is rebuilt on every usb run, so copy it to a directory of its own
on the stick. Several builds live side by side, each in a directory whose name
starts with the date it was produced:

```
/update/
├── 2026-07-02-my-deployment/
│   ├── flatcar/
│   ├── nodes/
│   ├── scripts/
│   └── ...
└── 2026-08-10-my-deployment/
    └── ...
```

The maintenance shell finds them by structure rather than by name: a directory
counts as a build when it holds `scripts/install-flatcar.sh`. The naming scheme
is therefore free, and only the ordering of the list depends on it.

Builds reach the stick in one of two ways. Either the directory is placed in
[`update/`](update) before the image is built, or it is copied onto the UPDATE
partition of a stick that has already been written. The second way is the usual
one once a stick exists, and is why the partition is grown to fill the device.

To include a build at build time, copy it into `update/` first:

```sh
cp -r /path/to/my-deployment/build/usb update/2026-10-20-my-deployment
```

To add one to a stick that is already written, mount its UPDATE partition and
copy it there:

```sh
sudo mount /dev/disk/by-label/IB-UPDATE /mnt
sudo cp -r /path/to/my-deployment/build/usb /mnt/2026-10-20-my-deployment
sudo umount /mnt
```

The size of a build is mostly the Flatcar image plus the docker images of its
nodes, so the partition is sized to its contents; see [Building](#building).
`update/` may also be left empty, in which case the UPDATE partition is created
at its minimum size of 1 GiB and grown when the stick is written.

## Using the USB on a node

1. Take the node out of service and insert the stick.
2. Select the USB device in the firmware boot menu.
3. Choose an entry in the GRUB menu. The default boots normally; a second entry
   adds `nomodeset` for hardware whose graphics fail to initialise; a third
   boots verbosely for diagnosis.
4. The live system starts `flatcar-maintenance.service`, which mounts the
   partition labelled `IB-UPDATE` at `/update`, lists the disks of the node and
   the builds on the stick, and opens a root shell inside the first build.
5. Install the node from the build of choice:

   ```sh
   cd /update/<build>
   ./scripts/install-flatcar.sh <node-name> [device]
   ```

   The node names are those under `nodes/` in that build, and the maintenance
   shell prints them. The target device defaults to `INSTALL_DEVICE` from the
   node's `node.env` (`install_device` in its `node.yml`). The script wipes that
   disk, installs Flatcar with the node's Ignition configuration, and copies the
   node's docker images and docker-compose onto the new root.
6. Reboot. The node comes up with its apps, but without secrets: send it its
   first app bundle (see [Updating a node](../README.md#updating-a-node)).
   Every later update goes the same way, never through this USB.

The kernel is given both a video and a serial console, and GRUB adds a serial
terminal when the node has a serial port, so a headless node can be serviced
over a serial line.

## Physical access

The USB grants root on any node it is booted on, without a password. That is
the purpose of the tool, but it makes the stick itself a credential: anyone
holding it can take over a node that will boot from USB. Store it as a physical
key, and consider the firmware settings of the nodes — boot order, a firmware
password, and whether USB boot is enabled at all — part of the same control.

The builds on the stick hold no secrets, but they are not encrypted either:
the Ignition configurations contain the SSH public keys and, if one is set,
the password hash of user `core`.

Root without a password is set by
[`live/hooks/9000-console-root.hook.chroot`](live/hooks/9000-console-root.hook.chroot).
Removing that hook restores a password prompt on the consoles that the
maintenance shell does not own.

## Limitations

**Secure Boot is not supported.** The UEFI bootloader is unsigned, and firmware
with Secure Boot enabled declines it without a message. The symptom is a stick
that the firmware lists but never boots, which is easy to mistake for a broken
image. Secure Boot has to be off on the node. Supporting it means shipping
`shimx64.efi.signed` as `EFI/BOOT/BOOTX64.EFI` together with Debian's signed
`grubx64.efi`, whose baked-in `/EFI/debian` prefix constrains the layout of the
EFI partition.

## Repository layout

| Path | Contents |
|------|----------|
| [`Dockerfile`](Dockerfile) | the builder image: live-build, GRUB, disk tooling and QEMU |
| [`build-usb-image.sh`](build-usb-image.sh) | the build itself, run inside the container |
| [`write-usb.sh`](write-usb.sh) | writes the image to a stick and adapts it to its size |
| [`live/package-lists/`](live/package-lists) | packages installed into the live system |
| [`live/bin/`](live/bin) | the maintenance shell |
| [`live/systemd/`](live/systemd) | the unit that starts it |
| [`live/hooks/`](live/hooks) | live-build chroot hooks |
| [`update/`](update) | USB builds to put on the UPDATE partition, bind-mounted at build time |
| [`output/`](output) | where the built image is written |

## Design notes

**GRUB is installed without `grub-install`.** Both bootloader images carry an
embedded early configuration that locates the LIVE partition by filesystem label
and hands over to `/boot/grub/grub.cfg` on it. `grub-install` instead probes the
device that holds the boot directory, which inside the container is a loop
device with no partition table and no discoverable relationship to the disk
being installed to. The UEFI image is produced with `grub-mkstandalone` and the
BIOS image with `grub-mkimage` followed by `grub-bios-setup`, which reads the
GPT of the image itself. Nothing in either image depends on how the image
happened to be attached during the build.

**Partitions are attached as separate loop devices.** Docker does not reliably
create `/dev/loopXpN` partition nodes, so each filesystem is attached with an
explicit offset and size limit. The whole image is attached once as well,
because `grub-bios-setup` needs to read the partition table. Given a plain file
rather than a block device it resolves the host filesystem instead, so the
build passes it the loop device and afterwards verifies that GRUB boot code
reached the MBR and the BIOS boot partition.

**`grub-bios-setup` is run from the LIVE partition, not from the container
filesystem.** It resolves the root device of its staging directory through
`/proc/self/mountinfo`, and for any path on Docker's overlay filesystem that
lookup returns the literal string `overlay`, which is not a path:
`failed to get canonical path of 'overlay'`. Staging `boot.img` and `core.img`
in `boot/grub/i386-pc/` on the mounted LIVE partition gives the lookup a loop
device to find, and is where `core.img` conventionally lives in any case.

The binary itself comes from `grub-pc-bin`, which installs it as
`/usr/lib/grub/i386-pc/grub-bios-setup`, outside `PATH`. The build resolves that
path by hand. The full `grub-pc` package does ship a `/usr/sbin` wrapper, but it
also runs debconf and wants to configure a real bootloader, which does not
belong in a builder image.

**The maintenance shell is not a `oneshot` unit.** `flatcar-maintenance.service`
runs `Type=idle` and declares `Conflicts=getty@tty1.service`. A `Type=oneshot`
unit whose command never returns — and an interactive shell never returns —
keeps `multi-user.target` from being reached, which presents as a hung boot. The
unit also uses `StandardInput=tty-force`: plain `tty` waits for whatever already
owns the terminal instead of taking it, so the shell would come up without
input.

**`metadata_csum_seed` and `orphan_file` are disabled on the ext4
filesystems.** GRUB 2.06 cannot read an ext4 filesystem that uses them, and the
resulting failure appears at boot as `unknown filesystem`. Debian bookworm
disables both by default, but the base image is not pinned to a Debian release.

**The video modules are built into both bootloader images.** Without them the
`linux` loader cannot set any mode, not even a text one, and reports `No
suitable video mode found. Booting in blind mode.` GRUB then hands the kernel no
video configuration and the console goes dark. `all_video` covers VBE and VGA on
BIOS and GOP and UGA on UEFI. The configuration keeps the firmware framebuffer
on UEFI, which has no text mode to fall back to, and forces a text console on
BIOS, which is the more reliable choice on unknown server hardware.

**The package list is driven by `flatcar-install`, not by taste.** That script
checks its dependencies up front and exits on the first one it cannot find, so
every tool in its `toolset` has to be present: `blockdev`, `btrfstune`, `gawk`,
`gpg`, `lsblk`, `lvm`, `udevadm`, `wget`, `wipefs` and the coreutils, plus
`bzip2` to unpack the image. Two are easy to miss. Debian provides `awk` through
`mawk`, which does not satisfy a check for `gawk`. And `lbzip2` is optional but
worth having: the script prefers it automatically and decompresses the image in
parallel, which saves minutes on every installation.

**Filesystem labels are prefixed.** A node under maintenance has its own disks
attached, and both GRUB and the maintenance shell select partitions by label.
`IB-LIVE` and `IB-UPDATE` are unlikely to collide where `LIVE` and `UPDATE`
would.

## Related documents

- [`../README.md`](../README.md) — Flatcar Builder: build modes, installing and
  updating a node
- [`../node/lib/install-flatcar.sh`](../node/lib/install-flatcar.sh) — the
  installer that ends up in `scripts/` of every USB build
