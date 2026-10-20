# Writing a deployment

A deployment directory describes everything Flatcar Builder needs to build your nodes:
which Flatcar version they run, who can log in, how each node is networked, and which
apps run on it with which files and secrets. The tool itself holds nothing specific to a
deployment, so this directory is the only thing you write.

This guide describes the directory, `deployment.yml` and `node.yml`. To check your work at
any point:

```sh
./build-flatcar.sh <deployment-dir> --check
```

`--check` runs every validation without downloading or building anything. An error names
the file and the field, for example `nodes/web-1/node.yml: apps[0].images[1].build.context`.

## Contents

- [The directory](#the-directory)
- [Paths](#paths)
- [deployment.yml](#deploymentyml)
- [node.yml](#nodeyml)
- [Network](#network)
- [Apps](#apps)
- [Compose files](#compose-files)
- [Files, secrets and directories](#files-secrets-and-directories)
- [Hooks](#hooks)
- [Data diodes](#data-diodes)
- [What reaches the node when](#what-reaches-the-node-when)

## The directory

```
my-deployment/
  deployment.yml              settings for the whole deployment
  keys/
    id_ed25519.pub            public SSH keys for user core
  common/
    files/                    optional: overlay onto / of every node
  nodes/
    web-1/                    one directory per node; its name is the node name
      node.yml                settings for this node
      compose.d/
        10-proxy.yml          compose file(s) per app
        50-web.yml
      files/                  optional: overlay onto / of this node
      secrets/                optional: secret overlay onto / of this node
      apps/
        web/files/            optional: files that belong to one app
        web/secrets/          optional: secrets that belong to one app
      hooks/
        web-reload.sh         optional: post-update hook of an app
  build/                      output of the builder; put it in .gitignore
```

Only `deployment.yml` and `nodes/<node>/node.yml` are required. The names `common/`,
`files/`, `secrets/`, `apps/` and `hooks/` are a convention: every one of them is named
explicitly in the YAML, so you may lay them out differently.

Keep `secrets/` directories out of git. They exist only on the build machine and in the
bundles built there.

The smallest working deployment is [`examples/minimal`](examples/minimal): one node, one app,
DHCP. Copy it as the start of your own and replace `keys/example.pub` with your own key.

## Paths

There are two kinds of path:

- **Paths on the build machine** point at files in your checkout. A relative path is
  relative to the file it is written in: paths in `deployment.yml` are relative to the
  deployment directory, paths in `node.yml` to that node's directory. So a build context
  two levels up is `../../dockers/web`. `~` is expanded.
- **Paths on the node** (`directories`, `replace_dirs`) are always absolute, such as
  `/etc/myapp`. `..` is refused.

## deployment.yml

```yaml
builder: ">=0.1,<1"            # which Flatcar Builder versions this deployment is written for

flatcar:
  channel: stable              # stable | beta | alpha | lts
  version: 4152.2.0            # a release, or "current" (pin a release for production)
  arch: amd64-usr              # amd64-usr | arm64-usr
compose_version: v2.39.1       # docker-compose release installed on the nodes

access:
  ssh_authorized_keys_files: [keys/id_ed25519.pub]
  core_password_hash: ""       # optional crypt hash for console login; never plaintext

logs:                          # optional; these are the defaults
  docker:  { max_size: 10m, max_file: 3 }
  journal: { max_use: 200M, max_file_size: 20M }

os_boot_check:                 # optional
  timeout: 10m

common_files: common/files     # optional

nodes:                         # optional; default: every directory in nodes/
  - web-1
  - web-2

diode_links: []                # optional; see "Data diodes"
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `builder` | no | — | Version range of Flatcar Builder, e.g. `">=1.0,<2"`. Comparisons `>=`, `>`, `<=`, `<`, `==`, `!=`, separated by commas. A build with a tool outside the range fails; without the field you get a warning. |
| `flatcar.channel` | yes | — | `stable`, `beta`, `alpha` or `lts`. |
| `flatcar.version` | yes | — | A release such as `4152.2.0`, or `current` to look up the newest release of the channel at build time. Pin a release for production: with `current`, two builds on different days can differ. |
| `flatcar.arch` | no | `amd64-usr` | `amd64-usr` or `arm64-usr`. |
| `compose_version` | yes | — | Release of the docker-compose binary for the nodes, written as `v2.39.1`. |
| `access.ssh_authorized_keys_files` | yes | — | List of files with public keys for user `core`. Each file may hold several keys; empty lines and `#` comments are skipped. At least one key is required: on a VPS, SSH is often the only way in. |
| `access.core_password_hash` | no | none | Password hash for user `core`, for logging in on the console. Must be a crypt hash starting with `$` (generate one with `openssl passwd -6`). Without it, `core` can only log in with a key. |
| `logs.docker.max_size` | no | `10m` | Maximum size of one container log file. A number with an optional `k`, `m` or `g`. |
| `logs.docker.max_file` | no | `3` | Number of log files kept per container (at least 1). |
| `logs.journal.max_use` | no | `200M` | Maximum disk space for the systemd journal. |
| `logs.journal.max_file_size` | no | `20M` | Maximum size of one journal file. |
| `os_boot_check.timeout` | no | `10m` | How long a node gets to come up healthy after an OS update, as `600s`, `10m` or `1h`. Read and validated, but the check that uses it is not built yet. |
| `common_files` | no | none | Directory with files that go onto every node; see [Files, secrets and directories](#files-secrets-and-directories). |
| `nodes` | no | every directory in `nodes/` | The nodes of this deployment, in order. Every name needs a directory `nodes/<name>/`. Use it to keep a node directory around without building it. Directories starting with `.` are always ignored. |
| `diode_links` | no | none | One-way links between nodes; see [Data diodes](#data-diodes). |

Unknown keys are an error, here and in `node.yml`, so a typo never goes unnoticed.

## node.yml

The name of the node directory is the node name. It may contain letters, digits, `.`, `_`
and `-`, and it is how you select the node on the command line (`--node web-1`) and how
bundles are named.

```yaml
hostname: web-1.example.org    # default: the node name
install_device: /dev/sda       # disk that the USB installation wipes and installs on

network:
  interfaces:
    - name: eth0
      mode: static
      addresses: [192.0.2.10/24, 2001:db8::10/64]
      gateways:  [192.0.2.1, 2001:db8::1]
      dns:       [192.0.2.53]

compose:
  project: web                 # default: derived from the node name

files: files                   # optional: overlay onto /
secrets: secrets               # optional: secret overlay onto /

directories:                   # optional: directories created on the node
  - { path: /var/log/myapp, mode: "0755" }

apps:
  - name: proxy
    compose: compose.d/10-proxy.yml
    images:
      - image: nginx:1.27-alpine
        pull: true
    files: apps/proxy/files
    replace_dirs: [/etc/proxy/conf.d]

  - name: web
    compose: compose.d/50-web.yml
    images:
      - image: example-web:latest
        build:
          context: ../../dockers/web
          args: { BASE_URL: /web/ }
      - image: postgres:16
        pull: true
    directories:
      - { path: /etc/web, mode: "0700" }
      - { path: /var/lib/web/db, mode: "0700" }
    secrets: apps/web/secrets
    hooks:
      post_update: hooks/web-reload.sh
```

| Field | Required | Default | Meaning |
|---|---|---|---|
| `hostname` | no | the node name | Written to `/etc/hostname` and available to compose files as `${HOSTNAME}`. |
| `install_device` | yes | — | The disk `install-flatcar.sh` installs on, such as `/dev/sda`, `/dev/vda` or `/dev/nvme0n1`. Can be overridden on the USB. Everything on it is erased. |
| `network.interfaces` | yes | — | At least one interface; see [Network](#network). |
| `compose.project` | no | from the node name | The compose project name. Lower case letters, digits, `_` and `-`; the default is the node name in lower case with other characters replaced by `-`. |
| `files` | no | none | Directory with files for this node; see [Files, secrets and directories](#files-secrets-and-directories). |
| `secrets` | no | none | Directory with secrets for this node. |
| `directories` | no | none | List of `{ path, mode }`: directories created on the node. |
| `apps` | no | none | The apps of this node, in stack order; see [Apps](#apps). |

A `mode` is always a **quoted** octal string, `"0755"` or `"755"`. An unquoted `755`
would be read as a decimal number, so it is refused.

## Network

Each interface becomes one systemd-networkd file on the node,
`/etc/systemd/network/<NN>-<name>.network`, numbered from 10 in the order of the list.
Network changes in a bundle take effect at the next boot of the node, not right away.

| Field | Default | Meaning |
|---|---|---|
| `name` | — | Required. The interface name. Matched on this name, unless `match_permanent_mac` is set. |
| `mode` | — | Required (except for diode interfaces): `dhcp` (IPv4 and IPv6), `dhcp4`, `dhcp6` or `static`. |
| `addresses` | none | Addresses with prefix length: `192.0.2.10/24`, `2001:db8::10/64`. Required for `static`; with a DHCP mode they are added next to the DHCP address. |
| `gateways` | none | Default gateways, IPv4 and IPv6 mixed. |
| `dns` | none | DNS servers. |
| `options` | none | Any of `onlink`, `ra`, `nocarrier`; see below. |
| `match_permanent_mac` | none | Match the card on its permanent MAC address instead of its name. Survives renamed interfaces and a MAC set with `mac`. |
| `mac` | none | Set a fixed MAC address on the interface. |
| `arp` | on | `arp: false` turns ARP off on the interface. |

The options:

- **`onlink`**: the gateway is not inside the subnet of the address, as at some VPS
  providers (a `/32` address with a gateway outside it). The gateway is then written as a
  route with `GatewayOnLink=yes`. A link-local IPv6 gateway (`fe80::1`) never needs this.
- **`ra`**: accept IPv6 router advertisements despite a static IPv6 address. Without it a
  static IPv6 address turns router advertisements off, so the written-down default route is
  the only one.
- **`nocarrier`**: configure the interface even without a link. Off by default, since on an
  ordinary link it would only hide a cable problem.

Examples:

```yaml
# DHCP on everything
- name: eth0
  mode: dhcp

# DHCP for IPv4, a fixed IPv6 address, gateway outside the subnet
- name: ens3
  mode: dhcp4
  addresses: [2001:db8:305:2100::1:287a/64]
  gateways:  [2001:db8:305:2100::1]
  dns:       [1.1.1.1, 2606:4700:4700::1111]

# Static, matched on the card instead of the name
- name: lan0
  match_permanent_mac: "00:1b:21:aa:bb:01"
  mode: static
  addresses: [10.0.0.5/24]
```

Write MAC addresses in quotes and as `aa:bb:cc:dd:ee:ff`.

## Apps

An app is a part of the node's compose stack, together with everything it needs: its
images, compose files, directories, files, secrets and hook. Apps run in the order they
are listed. An app can be updated on its own with `--mode app --app <name>`.

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Lower case letters, digits, `_` and `-`. Unique within the node. |
| `compose` | yes | One compose file or a list of them, ending in `.yml` or `.yaml`; see [Compose files](#compose-files). |
| `images` | no | The docker images the app's services use; see below. |
| `directories` | no | List of `{ path, mode }`, like the node's `directories`. |
| `files` | no | Directory with files that belong to this app. |
| `secrets` | no | Directory with secrets that belong to this app. |
| `replace_dirs` | no | Directories on the node that are emptied before the app's files are copied in; see [Files, secrets and directories](#files-secrets-and-directories). |
| `hooks.post_update` | no | Script that runs on the node after the app is updated; see [Hooks](#hooks). |

### Images

The nodes never contact a registry: every image is built or pulled on the build machine,
saved as a tar and shipped in the USB or the bundle. So every image a service uses has to
be listed with its app, in one of two forms:

```yaml
images:
  - image: postgres:16           # pulled from a registry on the build machine
    pull: true

  - image: example-web:latest    # built on the build machine
    build:
      context: ../../dockers/web
      dockerfile: ../../dockers/web/Dockerfile.prod   # optional; default: Dockerfile in the context
      args:                                           # optional; values are strings
        BASE_URL: /web/
```

- An image is either `pull: true` or `build`, never both.
- The build context must be a directory that is not empty. An empty context is usually a
  git submodule that is not checked out (`git submodule update --init --recursive`).
- The same image may appear in several apps and nodes, as long as its definition is the
  same everywhere; it is then built once. The same name with a different context,
  Dockerfile or build arguments is an error.
- Build arguments end up in the image and are readable with `docker history`. Never pass a
  secret this way.

## Compose files

Each app brings one or more ordinary docker compose files with a `services:` section. On
the node they are copied to `/opt/compose/compose.d/` under their file name, and the
builder generates `/opt/compose/compose.yml`, which includes them in the order of the apps:

```yaml
# Generated by Flatcar Builder from node.yml — edit that, not this.
name: web
include:
  - compose.d/10-proxy.yml
  - compose.d/50-web.yml
```

Rules:

- **Every `image:` of a service must be in the `images` of the same app**, or the tar would
  never reach the node. An image the app ships but no service uses gives a warning.
- **`build:` is not allowed** in a compose file. Images are built from `images` in
  `node.yml`.
- **File names must be unique within a node**, since all apps share `compose.d/`. A number
  prefix (`10-proxy.yml`, `50-web.yml`) keeps them apart and sorted.
- **`${NODE_NAME}` and `${HOSTNAME}`** are available as variables, so the node name never
  has to be written into a service.
- **Use a `restart:` policy** (for example `restart: unless-stopped`) on every service.
  `compose-stack.service` starts the stack at boot but does not watch it; docker restarts
  containers according to their policy.
- **Create the host directories you bind-mount** with `directories`. Otherwise docker
  creates a missing source as an empty directory owned by root, also where a file was meant.

When the docker compose plugin is installed on the build machine, the whole stack is
checked with `docker compose config`. Without it only the YAML is parsed, with a warning.

A minimal compose file, from `examples/minimal`:

```yaml
services:
  web:
    image: nginx:1.27-alpine
    container_name: web
    ports:
      - "80:80"
    volumes:
      - /etc/example/web:/usr/share/nginx/html:ro
    environment:
      NODE_NAME: ${NODE_NAME}
    restart: unless-stopped
```

## Files, secrets and directories

### Files

A `files` directory is an overlay onto `/` of the node: `files/etc/myapp/app.conf` ends up
as `/etc/myapp/app.conf`. There are three levels, all with the same form:

- `common_files` in `deployment.yml`: on every node;
- `files` in `node.yml`: on this node;
- `files` of an app: on this node, as part of that app.

Together with what the platform itself installs, they form one tree. **A path may come from
only one place**: the same file in two levels is an error that names both sources. The
exceptions are `/etc/docker/daemon.json` and `/etc/systemd/journald.conf.d/10-size.conf`,
which you may replace with your own. Files the builder generates (`/etc/hostname`,
`/etc/flatcar-builder/node.env`, the `.network` files and `/opt/compose/compose.yml`)
cannot be supplied.

Further:

- Only plain files: a symlink is an error.
- Files and directories whose name starts with `.`, and every `README.md`, are skipped, so
  you can document an overlay inside it.
- A file that is executable on the build machine gets mode 0755 on the node, any other file
  0644.
- On an update, files are overwritten and files that are no longer in the overlay stay on
  the node, unless they are in a `replace_dirs` directory.
- Units placed in `/etc/systemd/system/` are installed but not enabled. Run your software as
  a compose service instead.

### Secrets

A `secrets` directory has the same form as `files`, at node level and per app. The
difference is where it goes:

- **Never on the USB.** The Ignition configuration on the stick is not encrypted, so a
  freshly installed node has no secrets until its first app bundle.
- Installed as root:root with mode 0600. A directory created for a secret gets mode 0700.
- Only the file names appear in the logs, never the contents.
- A path may not be both a file and a secret.

### Directories

`directories` (on the node and per app) are created with the given mode, on installation
and by every bundle that carries them. An existing directory gets its mode set again, so a
changed mode reaches the node. The same path in several places is fine with the same mode
and an error with a different one.

### replace_dirs

A directory in an app's `replace_dirs` is emptied before the app's files are copied in. Use
it for directories whose contents are a set, such as a `conf.d/` with one file per site:
a file removed from the overlay then disappears from the node too. The path must be at
least two levels deep (`/etc/proxy/conf.d`, not `/etc`), as a protection against emptying
the wrong directory.

## Hooks

An app's `hooks.post_update` names a script on the build machine. It is shipped in the
bundle and runs on the node with `bash` after the stack has been brought up, for every app
in the bundle. It gets the environment variables `NODE_NAME` and `BUNDLE_DIR` (the
extracted bundle).

```yaml
hooks:
  post_update: hooks/web-reload.sh
```

```bash
#!/usr/bin/env bash
# Reload nginx so it picks up the new configuration.
set -euo pipefail
docker exec proxy nginx -t
docker exec proxy nginx -s reload
```

(`proxy` here is the `container_name` of a service.)

A failing hook is logged as a warning; the rest of the update stands. Hooks do not run on
the USB installation, and not in an os bundle that installs a new Flatcar version: there
the stack comes up after the reboot, without hooks.

## Data diodes

A data diode is a one-way link: frames go from one card to another, and nothing ever comes
back. That has consequences for the network configuration on both ends: the sender cannot
use ARP and sees no carrier, and the receiver needs a fixed MAC address so that the sender
can address it.

A link always involves two nodes, so it is described once, in `deployment.yml`, and the
builder writes both ends:

```yaml
diode_links:
  - name: ab
    from: { node: node-a, interface: diode-ab, address: 10.10.1.1/30 }
    to:   { node: node-b, interface: diode-ab, address: 10.10.1.2/30, mac: "02:00:0a:0a:01:02" }
```

| Field | Meaning |
|---|---|
| `name` | Name of the link; lower case letters, digits, `_` and `-`. |
| `from` | The sending end: `node`, `interface` and `address` (with prefix length). |
| `to` | The receiving end: the same, plus `mac`, the fixed MAC address the receiving card gets. |

In each `node.yml` the interface appears with only its name and, preferably, the card it
belongs to. Everything else comes from the link:

```yaml
network:
  interfaces:
    - name: eth0                      # ordinary management interface
      mode: dhcp
    - name: diode-ab
      match_permanent_mac: "00:1b:21:aa:bb:01"
```

Use `match_permanent_mac` on the receiving end: it matches the card by its own address,
so the fixed MAC set by the link does not stop the match.

Both ends get no ARP, no IPv6 link-local addressing or router advertisements, no LLDP, and
do not count towards "online", so the boot never waits for them. The sender also gets
`ConfigureWithoutCarrier` and a static neighbour entry with the receiver's address and MAC;
the receiver gets the fixed MAC.

The builder checks that:

- both nodes exist and have the interface in their `node.yml`;
- the two ends differ and are in the same subnet;
- the MAC is locally administered (second bit of the first octet set, e.g. `02:…`), not
  multicast, and unique over all links and interfaces;
- an interface belongs to at most one link;
- a diode interface has no `mode`, `addresses`, `options`, `mac`, `gateways` or `dns` in
  `node.yml`.

Any topology of one-way links works this way, for example a ring A → B → C → A of three
links. What travels over a link is up to the apps.

## What reaches the node when

| | USB (`--mode usb`) | Full bundle (`--mode app`, `--mode os`) | Bundle of selected apps (`--mode app --app X`) |
|---|---|---|---|
| Platform, generated files, network | yes | yes | no |
| `common_files`, node `files` | yes | yes | no |
| Node `secrets` | **no** | yes | no |
| `directories` of the node | yes | yes | no |
| Files, compose files, directories of an app | all apps | all apps | only X |
| Secrets of an app | **no** | all apps | only X |
| Images | all apps | all apps | only X |
| Hooks | no | yes (not when an os bundle installs a new Flatcar version) | only X |

A bundle of selected apps only works for an app that is already on the node. A new app
reaches a node with a full bundle (or the USB); after that it can be updated on its own.
