# Flatcar Builder — design

## 1. Goal

Flatcar Builder is a stand-alone tool that turns a **deployment directory** into installable Flatcar nodes and update bundles. The tool is on dependent on specific applications or deployments or any other application: everything specific lives in the deployment directory (the nodes) and in the docker apps. A new deployment changes nothing in the tool.

```
build-flatcar.sh ../server-deployment       --mode app --node neo-jmnl-node
build-flatcar.sh ../server-deployment --mode usb
```

### Functional requirements

1. Network configuration of the node (IPv4/IPv6, DHCP/static, onlink, RA, nocarrier).
2. Data diodes: fixed MAC addresses and static neighbours (ARP does not work over a diode),
   also in a ring of nodes (§4.4).
3. Config, files, environment and keys per application, within the node.
4. Build forms: USB (everything), OS + apps, apps only and selected apps — for all nodes or
   for selected nodes.
5. One bundle per node: a bundle is sealed for one node and contains only what that node
   needs.
6. OS updates always through Flatcar's A/B partitions, falling back to the previous version
   if the new one does not come up properly (§3.3). The USB is only for the first
   installation.

## 2. Three layers

| Layer | Where | Knows about |
|---|---|---|
| **Platform** | Flatcar Builder (now `flatcar-deployment-new/`, later its own repo, §8) | Installing and updating Flatcar, network, SSH, docker + compose, offline images, the compose unit, the watcher, auto-updates off, log caps. Generic mechanisms: files, secrets, directories, hooks. |
| **Deployment** | e.g. `jmnl-deployment/`, `ironbridge-deployment/` | Which nodes, which Flatcar version, SSH keys, diode links; per node: network, apps, compose, files, secrets. |
| **Apps** | `dockers/<app>/`, `projects/<app>/` | Dockerfile, build context, own documentation. Referenced from the deployment. |

## 3. What the platform puts on the node

| Path / unit | What |
|---|---|
| `compose-stack.service` | Starts the compose stack at boot (§3.1). |
| `watch-install.service` | Runs `/opt/bin/watch-install.sh` (§3.2). |
| `import-images.service` | Loads `/opt/images/*.tar` into docker at boot. |
| `os-boot-check.service` | After an OS update: confirms the new partition or falls back (§3.3). **Not built yet** (waits for TASKS 5.1). |
| `/opt/deploy/` | The directory the watcher watches. |
| `/opt/deploy/work/` | Extracted bundles (last 3 kept). |
| `/opt/compose/compose.yml`, `compose.d/` | The node's stack. |
| `/opt/images/` | Image tars. |
| `/opt/bin/docker-compose`, `/opt/bin/watch-install.sh`, `/opt/bin/health-check.sh` | Platform binaries. |
| `/etc/flatcar-builder/node.env` | `NODE_NAME`, `HOSTNAME`, … — environment for the compose unit. |
| `/var/lib/flatcar-builder/apps/<app>.json` | What is installed: one file per app, with images, services, compose parts and the bundle it came from. One file per app, so bash can check it without a JSON parser (does it exist?) and a bundle carries it as an ordinary file. |
| `/var/lib/flatcar-builder/last-bundle.json` | The manifest of the most recently installed bundle. |
| `/var/log/watch-install.log` | Log of `watch-install.sh`. |
| `/var/log/install.log` | Log of the bundle's `install.sh` (and the scripts it calls). |
| `/etc/docker/daemon.json`, `/etc/systemd/journald.conf.d/10-size.conf` | Log caps (default 3×10 MB per container, journal 200 MB; overridable). |
| `/etc/flatcar/update.conf` + masks | Flatcar never updates on its own, only through a bundle. |

Log files are named after the script that writes them. A logging container that wants to
collect them mounts `/var/log` (or the individual files) read-only; the platform itself
sends nothing over the network.

### 3.1 `compose-stack.service`

The systemd unit that brings up the node's compose stack at boot:
`docker-compose -f /opt/compose/compose.yml up -d --remove-orphans`, then done
(`Type=oneshot`, `RemainAfterExit=yes`, `Restart=on-failure` only for a failed `up`).
It does not watch the containers — docker does that through each service's `restart:`
policy, so one container falling over does not take the rest with it. `ExecStop` does
`stop`, not `down`. `EnvironmentFile=/etc/flatcar-builder/node.env` makes `${NODE_NAME}`
available in the compose files, so the node name is never hard-coded in a service.

### 3.2 `watch-install.sh`

Belongs to the platform and watches `/opt/deploy`:

- a `<name>.tar` with a `<name>.tar.approved` is installed;
- `<name>.tar.installed` is written **before** the installation (one approval = one
  attempt, even if the installation reboots the node);
- extract into `/opt/deploy/work/<name>`, run `install.sh` from its root;
- clean up old extracted bundles (`KEEP_WORK_DIRS`, default 3).

It logs to stdout (journald) and to `/var/log/watch-install.log`, not to the network.
Whoever places the tar and the `.approved` file (on neo: the firmware-update containers
that verify the signature) is an app, not the platform.

### 3.3 OS updates through A/B

Flatcar has two `/usr` partitions (USR-A and USR-B): one is running, the other is free.
An OS update never writes over the running partition and never wipes the disk; only the USB
installation does that, and it exists only for an empty machine.

1. A `--mode os` bundle contains `flatcar_production_update.gz` and `TARGET_FLATCAR_VERSION`.
2. `install.sh` first applies the apps, then `lib/update-os.sh`:
   `flatcar-update --to-version <V> --to-payload <payload>` writes the payload to the free
   partition and sets it up for the next boot with one attempt. Afterwards
   `update-engine` is masked again.
3. `install.sh` reboots the node. The new partition boots.
4. `os-boot-check.service` waits for docker, `compose-stack` and `health-check.sh`
   (timeout configurable, default 10 min).
   - **Healthy:** the boot is marked successful; the new partition stays.
   - **Not healthy:** reboot. The attempt is used up, so the bootloader starts the previous
     partition.
   - A boot that never gets that far (kernel, systemd) falls back through the same counter.
5. If the node is already at the target version, `install.sh` skips the OS step.

**Verify in a VM first, before neo:** normally `update-engine` marks a boot as successful,
and it is masked here. To find out: (a) what happens to a new partition when nobody marks
it — does it stay, or does the node fall back at the next reboot; (b) which command
`os-boot-check` uses to mark it (`cgpt add -S 1 …` on the USR partition or
`update_engine_client`). The rest of the design does not depend on this, but §3.3 does.

## 4. Deployment directory

```
jmnl-deployment/
  deployment.yml
  keys/                       id_ed25519.pub (git), …
  common/
    files/                    overlay onto / on EVERY node, public
  nodes/
    neo-jmnl-node/
      node.yml
      compose.d/*.yml         one or more compose parts per app
      files/                  overlay onto /, public (ignition + bundle)
      secrets/                overlay onto /, secret (bundle only, never in git)
      apps/<app>/files/       optional: files that belong to one app
      apps/<app>/secrets/
      hooks/*.sh
```

Relative paths are resolved relative to the file they appear in
(`deployment.yml` → deployment directory, `node.yml` → node directory).

### Build output

The output lives in the deployment directory, not in a node directory and not in the tool:

```
jmnl-deployment/
  build/                      in .gitignore; elsewhere with --out
    work/<node>/              intermediate result per node; wiped at the start of every run
    usb/                      contents of the USB stick (--mode usb); wiped on every usb run
    bundles/                  the tars (--mode os/app); kept
      neo-jmnl-node-app-20261020-1430.tar
      neo-jmnl-node-app-intento-20261020-1512.tar
```

`work/` and `usb/` are rebuilt, so nothing from an earlier run is ever left behind.
`bundles/` is not wiped: every tar has a timestamp and is a release you may still want to
upload or roll back to. Cleaning up is up to you.

The download cache (Flatcar images, payloads, compose binary) lives outside the deployment
in `~/.cache/flatcar-builder/`, so several deployments share it.

### 4.1 `deployment.yml`

```yaml
builder: ">=1.0,<2"          # which Flatcar Builder version this deployment expects (§8)

flatcar:
  channel: stable
  version: 4152.2.0          # "current" is allowed, but pin for production
  arch: amd64-usr
compose_version: v2.39.1

access:
  ssh_authorized_keys_files: [keys/id_ed25519.pub]
  core_password_hash: ""     # crypt hash ($6$…), never plaintext

logs:                        # optional; these are the defaults
  docker: { max_size: 10m, max_file: 3 }
  journal: { max_use: 200M, max_file_size: 20M }

os_boot_check:
  timeout: 10m

common_files: common/files   # optional

nodes:                       # optional; default: every directory in nodes/
  - neo-jmnl-node

diode_links: []              # optional, see §4.4
```

### 4.2 `node.yml` (neo.jmnl.nl)

```yaml
hostname: neo.jmnl.nl          # default: the directory name
install_device: /dev/sda

network:
  interfaces:
    - name: ens3
      mode: dhcp4              # dhcp | dhcp4 | dhcp6 | static
      addresses: [2001:41d0:305:2100::1:287a/64]
      gateways:  [2001:41d0:305:2100::1]
      dns:       [1.1.1.1, 2606:4700:4700::1111]
      options:   []            # onlink | ra | nocarrier

compose:
  project: neo                 # default: the node name

files: files                   # optional
secrets: secrets               # optional

directories:                   # created by ignition and by every bundle
  - { path: /var/log/app, mode: "0755" }

apps:
  - name: logging
    compose: compose.d/10-logging.yml
    images:
      - image: logging-proxy:latest
        build: { context: ../../../dockers/logging/logging-proxy }

  - name: intento
    compose: compose.d/80-intento.yml
    images:
      - image: intento-web:latest
        build:
          context: ../../../projects/intento
          dockerfile: ../../../projects/intento/web/Dockerfile
          args: { VITE_BASE: /intento/, VITE_API_URL: /intento/api }
      - image: postgres:15
        pull: true
    directories:
      - { path: /etc/jmnl/intento, mode: "0700" }
    secrets: apps/intento/secrets

  - name: web
    compose: compose.d/50-web.yml
    images: [ … ]
    directories:
      - { path: /etc/jmnl/letsencrypt, mode: "0700" }
      - { path: /var/lib/acme/webroot, mode: "0755" }
    files: apps/web/files            # etc/jmnl/nginx/conf.d/*.conf
    replace_dirs: [/etc/jmnl/nginx/conf.d]
    hooks:
      post_update: hooks/nginx-test.sh
```

Paths such as `/etc/jmnl/...` are this node's choice; the platform does not know them.

The image definitions stay per node, grouped per app. The same image in two apps
(e.g. `postgres:15`) is allowed; across all nodes every image is built once, and a
conflict (same name, different context/dockerfile/args) is an error.

The root `compose.yml` is **generated** from `compose.project` and the order of `apps`
(`name:` + `include:`). Adding an app is one entry in `node.yml`.

### 4.3 Overlays: one source, two channels

Per node the build assembles one tree that ends up on `/`:

```
platform rootfs   (units, watch-install.sh, daemon.json, journald, update.conf)
+ common/files
+ node files
+ app files
+ generated       (systemd .network files, node.env)
```

Ignition contains that tree at installation, and every full bundle contains it too. A
change to a unit, a log cap or a vhost therefore reaches a running node the same way as a
new one. A path that appears in two layers is an error, except platform files that are
deliberately overridden (`/etc/docker/daemon.json`).

`secrets` (node and app) go **only** into the bundle, root:root 0600 in 0700 directories,
and never into ignition (which sits unencrypted on the USB). A freshly installed node
therefore has secrets only after its first app bundle.

`replace_dirs` are replaced as a whole (`/opt/compose/compose.d` always); in all other
directories files are overwritten and extra files on the node stay.

### 4.4 Data diodes in a ring

Setup: **A → B → C → A**. Three one-way links; each node has one outgoing and one incoming
diode interface (next to an ordinary management interface, if any).

```
        link ab            link bc
   A ────────────► B ────────────► C
   ▲                               │
   └───────────────────────────────┘
               link ca
```

Nothing ever comes back over a diode, so:

- The **sender** cannot do ARP → a static neighbour (IP → MAC of the receiver). It usually
  sees no carrier (no light coming back) → configure without carrier.
- The **receiver** gets a **fixed MAC**, so the static neighbour on the sender is right,
  even after a network card is replaced.
- Both ends: no ARP, no IPv6 link-local/RA, no LLDP, and the link does not count towards
  "online" (otherwise the boot waits for a link that is never reported "up").

A link spans two nodes, so it is described **once**, in `deployment.yml`, and the tool
writes both ends. That way sender and receiver cannot drift apart:

```yaml
diode_links:
  - name: ab
    from: { node: node-a, interface: diode-ab, address: 10.10.1.1/30 }
    to:   { node: node-b, interface: diode-ab, address: 10.10.1.2/30, mac: "02:00:0a:0a:01:02" }
  - name: bc
    from: { node: node-b, interface: diode-bc, address: 10.10.2.1/30 }
    to:   { node: node-c, interface: diode-bc, address: 10.10.2.2/30, mac: "02:00:0a:0a:02:02" }
  - name: ca
    from: { node: node-c, interface: diode-ca, address: 10.10.3.1/30 }
    to:   { node: node-a, interface: diode-ca, address: 10.10.3.2/30, mac: "02:00:0a:0a:03:02" }
```

`node.yml` only says which physical card the interface is — by its **permanent** MAC, so
the receiver's fixed MAC does not break the match:

```yaml
network:
  interfaces:
    - name: diode-ab
      match_permanent_mac: "00:1b:21:aa:bb:01"
    - name: diode-ca
      match_permanent_mac: "00:1b:21:aa:bb:02"
```

What the tool makes of that (A, sender on `ab`):

```ini
[Match]
PermanentMACAddress=00:1b:21:aa:bb:01
[Link]
ARP=no
RequiredForOnline=no
[Network]
Address=10.10.1.1/30
ConfigureWithoutCarrier=yes
IgnoreCarrierLoss=yes
LinkLocalAddressing=no
IPv6AcceptRA=no
LLDP=no
EmitLLDP=no
[Neighbor]
Address=10.10.1.2
LinkLayerAddress=02:00:0a:0a:01:02
```

(B, receiver on `ab`): the same `[Match]` form, `[Link] MACAddress=02:00:0a:0a:01:02`,
`ARP=no`, `RequiredForOnline=no`, the address, no neighbour and no carrier options.

Checks on the build machine: both nodes and interfaces exist; both addresses in the same
subnet; MACs unique and locally administered (second bit of the first octet set); an
interface belongs to at most one link; diode interfaces have no gateway or DNS.
A ring is not enforced — any topology of one-way links works this way.

What travels over the link (UDP, a diode protocol) is the apps' business, not the
platform's.

## 5. Build forms

```
build-flatcar.sh <deployment-dir> [--mode usb|os|app] [--node N]... [--app A]...
                 [--out DIR] [--check]
```

| | Flatcar image + installer | OS payload | platform + node overlay | apps | reboot |
|---|---|---|---|---|---|
| `--mode usb` | ✓ | – | ✓ (ignition) | all | n/a |
| `--mode os` | – | ✓ | ✓ | all | yes, A/B (§3.3) |
| `--mode app` | – | – | ✓ | all | no |
| `--mode app --app X` | – | – | – | only X | no |

- `--node` may be repeated; default all nodes from `deployment.yml`.
- `usb` makes **one** directory `<out>/usb/` for all chosen nodes (shared image pool, one
  ignition per node). Only for an empty machine: `flatcar-install` wipes the disk.
- `os`/`app` make **one tar per node**:
  `<out>/bundles/<node>-<mode>[-<apps>]-<YYYYmmdd-HHMM>.tar`.
- `--app` only with `--mode app`, may be repeated, and every app must exist on every chosen
  node (otherwise an error naming the node).
- `--check` only validates: no download, no build.

An app bundle never reboots the node: `install.sh` applies it with `up -d`, and only
services whose image or configuration changed are recreated. A changed platform unit is
installed, `systemctl daemon-reload` and `enable`, but **not** restarted: restarting
`compose-stack` stops the whole stack, and restarting `watch-install` aborts the running
`install.sh`. It takes effect from the next boot. The same holds for a changed network
configuration and for the docker log cap; the journal cap takes effect immediately.

The USB contains no OS payload: an empty machine gets the image itself, and an OS update
always goes through an os bundle.

### 5.1 Selected apps

A bundle with `--app intento` contains only: the images of intento, the compose part of
intento, and the files/secrets/directories/hooks of intento. The root `compose.yml` and the
other parts stay on the node as they are.

On the node:

1. `install.sh` checks whether `/var/lib/flatcar-builder/apps/<app>.json` exists. If not:
   refuse with "new app — use a full app bundle" (the root `compose.yml` does not know the
   part yet). This is a fixed rule: an app reaches a node through a full app bundle (or the
   USB), and is updated on its own after that.
2. Load images, replace the part, apply overlays.
3. `docker-compose up -d --no-deps <services of that app>` — the services are in the
   manifest; the rest of the stack is not touched.
4. Hooks of the app; the app file comes along in the bundle and is simply overwritten.

## 6. Bundle layout

```
install.sh                 entrypoint for the watcher (must be at the root)
MANIFEST.json              node, mode, apps, services per app, images, flatcar version,
                           build time, version + git commit (+ dirty) of builder and deployment
bundle.env                 the same for install.sh (BUNDLE, NODE, MODE, PARTIAL, APPS, …)
rootfs/                    → /   full: the whole tree from §4.3, including /opt/compose and the
                                 app files; selected apps: only their files,
                                 compose parts and app file
secrets/                   → /   0600; full: node + apps, selected: those apps
directories.list           <mode>\t<path>
replace_dirs.list          <path>   emptied first, then filled from rootfs/
units.list                 units that are enabled (full only)
images/*.tar, images.list  only the images of this node / these apps
apps/<app>/services        the services of the app, for `up -d --no-deps`
apps/<app>/hooks/post-update.sh
bin/docker-compose         (full only)
updates/                   flatcar_production_update.gz + TARGET_FLATCAR_VERSION (os)
lib/                       update-app.sh, update-os.sh, disable-auto-updates.sh
checksums.sha256
```

On the node `install.sh` first checks, before anything changes: the checksums, that the
bundle is for *this* node (`NODE` against `/etc/flatcar-builder/node.env`), and for
selected apps that they are already there. If the app update fails, `install.sh` stops and
no OS update follows.

## 7. Structure of the tool

```
flatcar-builder/
  build-flatcar.sh            entrypoint (§8)
  VERSION
  builder/                    build side (Python 3, stdlib + PyYAML)
    __main__.py, cli.py       arguments, phases
    config.py, schema.py      load YAML, validate, resolve paths
    version.py                the builder: range from deployment.yml
    network.py                .network files, including diode links
    compose.py                generate root, validate (docker compose config), images per part
    images.py                 collect, deduplicate, build/pull/save
    fetch.py                  Flatcar artefacts and compose binary, with cache
    rootfs.py                 assemble overlays, detect conflicts, platform files
    butane.py                 dict → YAML → butane container → ignition
    bundle.py                 layout, manifest, checksums, tar per node
    usb.py                    the USB layout
    gitinfo.py                commit + dirty for the manifest
  node/                       runs ON the node (bash; Flatcar has no Python)
    install.sh
    lib/{update-app,update-os,disable-auto-updates,install-flatcar}.sh
    rootfs/etc/systemd/system/{compose-stack,watch-install,import-images}.service
    rootfs/opt/bin/{watch-install.sh,health-check.sh}
                              daemon.json, journald cap, update.conf and node.env
                              are generated (rootfs.py)
  examples/minimal/           deployment with one node and one app
  tests/                      python -m unittest; end-to-end with stub docker
  README.md
  DESIGN.md
```

The build side is Python: reading YAML, validating and generating (butane, `.network`)
become ordinary data structures instead of `printf | sed`. The node scripts are ordinary
files in `node/` instead of heredocs, so shellcheck and tests can check them.

### Phases of a build

1. Load the config and validate **everything** (all chosen nodes, diode links) — nothing
   built yet.
2. Determine the Flatcar version, fetch artefacts (cache).
3. Collect images over the chosen nodes/apps, deduplicate, build/pull, `docker save`.
4. Per node: assemble rootfs, generate compose, `MANIFEST.json`.
5. `usb`: butane → ignition per node, `install-flatcar.sh`, shared image pool.
6. `os`/`app`: bundle per node, checksums, tar (check: `install.sh` at the root).

### Validation on the build machine

Schema of `deployment.yml`/`node.yml` with file and field in the error message; network and
diode links (§4.4); compose per part and as a whole; every `image:` in a part is in the
`images` of that app, and every image of an app is used (warning); unique app names;
overlays without conflict; no file in both `files` and `secrets`; `--node`/`--app` exist;
conflicts in the build definition; the `builder` version in `deployment.yml` matches the
tool.

## 8. Distribution and use on the build machine

Flatcar Builder becomes its own git repo. It does **not need to be installed**:
`build-flatcar.sh` works from any checkout. It finds its own directory, checks `python3`
(≥ 3.9), PyYAML and docker, and starts `python3 -m builder` from that directory. If PyYAML
is missing, it says how to install it (`apt install python3-yaml`).

How a deployment uses the tool — the choice:

| | How | For | Against |
|---|---|---|---|
| a | Separate checkout, call the script | Simplest | Which version built this bundle? Depends on the checkout on that machine. |
| **b** | **Git submodule in the deployment repo** | Version pinned per deployment commit; `git clone --recursive` and build; no installation | One `git submodule update` after an upgrade |
| c | `pipx install git+…@v1.2` | A `build-flatcar` command everywhere | Version per machine, not per deployment; extra step |
| d | Docker image with the tool | All dependencies included | Docker socket + path translation of build contexts; more complicated than needed |

**Proposal: b.** The deployment repo pins which version of the tool it uses, just as it
pins the version of each app (`projects/*` are already submodules). A bundle can then
always be traced back to one commit. In addition the tool checks the `builder:` range from
`deployment.yml`, and writes its version and commit into `MANIFEST.json`.

What the JMNL repo could then look like — this repo becomes the deployment repo, because
the apps (`dockers/`, `projects/`) are already in it:

```
jmnl-external-servers/
  flatcar-builder/          submodule, at tag v1.x
  deployment/               deployment.yml, keys/, common/, nodes/neo-jmnl-node/
  dockers/
  projects/
```

```
git clone --recursive <jmnl-external-servers>
cd jmnl-external-servers
./flatcar-builder/build-flatcar.sh deployment --mode app --node neo-jmnl-node
```

An `ironbridge-deployment` looks the same, with the same submodule at possibly a different
tag. Until the tool is stable, we develop it in `flatcar-deployment-new/` in this repo;
after that we split it off into its own repo (`git subtree split` keeps the history) and
hang it back in as a submodule.

## 9. Build order

1. Skeleton, `config.py` with schema, `--check`, `examples/minimal`.
2. `network.py` (port the current tests + fixed MAC, static neighbours, diode links),
   `butane.py`, `--mode usb`.
3. `images.py`, `bundle.py`, `--mode app` per node; `node/` scripts, `watch-install.sh`.
4. Selected apps: manifest with services, app files, partial installation.
5. `--mode os` + `os-boot-check` — first the verification from §3.3 in a VM.
6. Convert neo to a deployment directory; reinstall neo with the USB.
7. Split the tool off into its own repo; remove the old `flatcar-deployment/`.

## 10. Open points

- **A/B marking** (§3.3) — first find out in a VM how a boot is marked successful when
  `update-engine` is masked.
- **Distribution** (§8) — confirm proposal b (submodule), and whether this repo becomes the
  JMNL deployment repo.
- **Data diodes** (§4.4) — confirm the assumptions: every arrow is its own physical diode
  with its own network card on both sides; the diode passes Ethernet frames through as they
  are (so the receiver's MAC counts); the sender sees no carrier; only IPv4 over the
  diodes. **Built according to these assumptions**; if one is wrong, only `render_diode` in
  `builder/network.py` changes.
- **Verifying downloads** — Flatcar publishes `.DIGESTS` and signatures, GitHub a `.sha256`
  next to the compose binary. Currently only the length is checked.
- **Zabbix catalogue and `cicd.pub` in `common_files`** — these go into every full bundle,
  but not into a bundle of selected apps.
