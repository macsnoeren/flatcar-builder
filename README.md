# Flatcar Builder

Flatcar Builder turns a **deployment directory** into stand-alone [Flatcar Container
Linux](https://www.flatcar.org/) nodes that run their applications with docker compose.
The tool itself knows nothing about the applications: everything specific to a deployment
(its nodes, network, apps, files and secrets) lives in the deployment directory, so a new
deployment needs no change to the tool.

The repository has two parts:

- **The builder** ([`build-flatcar.sh`](build-flatcar.sh), [`builder/`](builder),
  [`node/`](node)) reads a deployment directory and produces either the contents of an
  installation USB for empty machines, or a sealed update bundle per node.
- **The USB installer** ([`usb-installer/`](usb-installer)) builds a bootable Debian live
  image that carries those USB contents, so a node can be installed from a stick.

```
deployment dir ──► build-flatcar.sh --mode usb ──► build/usb/ ──► usb-installer ──► stick ──► empty machine
               └─► build-flatcar.sh --mode os|app ──► build/bundles/<node>-….tar ──► /opt/deploy on the node
```

## Requirements

Nothing to install: run it from a checkout. The build machine needs:

- python3 >= 3.9 with PyYAML (`apt install python3-yaml`);
- docker, for building and pulling the app images and for running butane. `--check` and
  `--help` work without it.

Downloads (Flatcar image and installer, OS update payloads, the docker-compose binary) are
cached in `~/.cache/flatcar-builder/` and shared between deployments.

## Quick start

[`examples/minimal`](examples/minimal) is the smallest deployment: one node, one app
(nginx), DHCP. Replace `keys/example.pub` with your own key and copy it as the start of a
new deployment.

```sh
./build-flatcar.sh examples/minimal --check       # validate only: no download, no build
./build-flatcar.sh examples/minimal --mode usb    # contents of the installation USB
./build-flatcar.sh examples/minimal --mode app    # one app bundle per node
```

## Build modes

```
build-flatcar.sh <deployment-dir> [--mode usb|os|app] [--node N]... [--app A]...
                 [--out DIR] [--check]
```

| Mode | What it produces | Apps | Reboots the node |
|---|---|---|---|
| `--mode usb` (default) | `build/usb/`: Flatcar image, installer, one Ignition config per node, shared image pool | all | n/a — first install only, wipes the disk |
| `--mode os` | one tar per node: OS update payload + platform + apps | all | yes, into the other A/B partition |
| `--mode app` | one tar per node: platform + apps | all | no |
| `--mode app --app A [--app B]` | one tar per node: only these apps | only A, B | no |

- `--node` may be repeated; the default is every node of the deployment.
- `--app` only works with `--mode app`, and each app must exist on every chosen node.
- Output goes to `<deployment-dir>/build/` unless `--out` says otherwise: `work/` and `usb/`
  are rebuilt on every run, `bundles/` is kept (each tar is timestamped).

## Deployment directory

```
my-deployment/
  deployment.yml            Flatcar version, SSH keys, log caps, diode links, builder version range
  keys/                     public SSH keys
  common/files/             optional overlay onto / of every node
  nodes/<node>/
    node.yml                hostname, install device, network, apps
    compose.d/*.yml         compose part(s) per app
    files/                  overlay onto / (Ignition and bundles)
    secrets/                overlay onto /, bundles only — never on the USB, never in git
    apps/<app>/files/       files that belong to one app
    hooks/*.sh              post-update hooks
```

Every file and field is described in [DEPLOYMENT.md](DEPLOYMENT.md);
[`examples/minimal`](examples/minimal) shows them in use. A real deployment is best
kept in its own git repo with this repo as a submodule, so every bundle traces back to one
commit of both.

## Installing a node

For an **empty** machine only: `flatcar-install` repartitions the whole disk.

1. `build-flatcar.sh <deployment> --mode usb` writes the USB contents to
   `<deployment>/build/usb/`.
2. Build the bootable stick with [`usb-installer/`](usb-installer/README.md) and copy the
   USB contents onto its UPDATE partition.
3. Boot the machine from the stick. The maintenance shell lists the builds and nodes on it;
   install with `./scripts/install-flatcar.sh <node> [device]` and reboot.
4. Send the node its first app bundle. The USB carries no secrets, so a freshly installed
   node gets them only with that bundle.

A rescue system with the `build/usb/` contents works as well: run
`bash scripts/install-flatcar.sh <node> [device]` as root.

## Updating a node

An installed node is only ever updated with a bundle. Place `<name>.tar` in `/opt/deploy`
on the node and then `<name>.tar.approved` next to it. `watch-install.service` installs
each approved bundle exactly once, writes `<name>.tar.installed` with the outcome, and logs
to `/var/log/watch-install.log` and `/var/log/install.log`. Who places the tar and the
approval (for example a container that checks a signature) is up to the deployment.

Before changing anything the bundle's `install.sh` checks the checksums and that the bundle
was built for this node. An app bundle never reboots the node: only services whose image or
configuration changed are recreated. An os bundle writes the new Flatcar version to the
spare A/B partition and reboots into it. Automatic Flatcar updates are disabled on every
node.

## Status

Version 0.1.0 ([`VERSION`](VERSION)). The builder side and the node scripts are built and
covered by tests; the manual checks in a VM are still open. Not built yet:
`os-boot-check.service`, which confirms a new OS partition after an os bundle or falls back
to the previous one. Until then, treat `--mode os` as
untested on real nodes.

## Repository layout

| Path | Contents |
|---|---|
| [`build-flatcar.sh`](build-flatcar.sh) | entrypoint; checks python3 and PyYAML, then runs `python3 -m builder` |
| [`builder/`](builder) | build side (Python, stdlib + PyYAML): config, network, compose, images, butane, bundles, USB layout |
| [`node/`](node) | runs on the node (bash): `install.sh`, `lib/*.sh`, the platform units and `watch-install.sh` |
| [`usb-installer/`](usb-installer) | builds the bootable maintenance USB image |
| [`examples/minimal/`](examples/minimal) | the smallest deployment |
| [`tests/`](tests) | unit and end-to-end tests with stub docker, systemctl and flatcar tools |

## Tests

```sh
bash tests/run.sh                                  # no internet, no docker daemon
bash tests/run.sh -k network                       # only tests matching a name
FLATCAR_BUILDER_INTEGRATION=1 bash tests/run.sh    # also against the real butane
```

`tests/run.sh` also runs shellcheck over the shell scripts when it is installed.

## License

GPL-3.0, see [LICENSE](LICENSE).
