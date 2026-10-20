# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Flatcar Builder turns a *deployment directory* (`deployment.yml` + `nodes/<node>/node.yml`) into
installable Flatcar nodes that run their apps with docker compose. The tool must stay generic:
nothing about a specific deployment or application belongs in `builder/` or `node/`.
`usb-installer/` is a separate, self-contained part that builds a bootable Debian live USB which
carries the builder's `--mode usb` output.

## Commands

No build step and nothing to install; needs python3 >= 3.9 with PyYAML (and docker only for real builds).

```sh
bash tests/run.sh                                   # all tests + shellcheck (~2 min, no internet, no docker daemon)
bash tests/run.sh -k network                        # tests whose name matches; skips shellcheck
PYTHONPATH=. python3 -m unittest tests.test_network.Diodes   # one class (or .test_method)
FLATCAR_BUILDER_INTEGRATION=1 bash tests/run.sh -k real_butane   # also against the real butane container

./build-flatcar.sh examples/minimal --check         # validate only: no download, no build
./build-flatcar.sh examples/minimal --mode usb|os|app [--node N] [--app A]   # output in examples/minimal/build/
```

`tests/run.sh` runs shellcheck over an explicit list of scripts (`build-flatcar.sh`, `node/install.sh`,
`node/lib/*.sh`, `node/rootfs/opt/bin/*.sh`, `tests/run.sh`). Add new shell scripts to that list.
`usb-installer/` scripts are not linted or tested; its image is built with
`docker build -t flatcar-maintenance-builder usb-installer` and a `--privileged` `docker run` (see its README).

## Architecture

**Two runtimes.** `builder/` (Python, stdlib + PyYAML only) runs on the build machine. `node/` (bash only;
Flatcar has no Python) runs on the node. They meet in the bundle layout, so a change to what
`builder/bundle.py` writes almost always needs a matching change in `node/install.sh` /
`node/lib/update-app.sh`, and the other way round. The contract files: `bundle.env` (sourced by bash),
`MANIFEST.json`, `directories.list` (`<mode>\t<path>`), `replace_dirs.list`, `units.list`, `images.list`,
`apps/<app>/services`, `apps/<app>/hooks/post-update.sh`, `checksums.sha256`, `install.sh` at the tar root.

**Phases (`builder/cli.py: run`).** Load and validate *everything* for all chosen nodes first
(`config.load_deployment`, then `network`, `compose`, `rootfs.validate`, `images.collect`) so `--check`
catches every error before any download. Then fetch (cached), build/pull/save images once across nodes,
then per node either the USB entry (`usb.py` + `butane.py`) or a bundle (`bundle.py`).

**Config and errors.** `config.py` turns YAML into dataclasses; every read goes through `schema.Ctx`,
which carries file + field path so errors read `nodes/x/node.yml: apps[0].images[1].build.context: …`.
Unknown keys are errors: adding a field means updating the allowed-key sets (`DEPLOYMENT_KEYS`,
`NODE_KEYS`, `APP_KEYS`, the `as_map(..., allowed)` calls), the dataclass, and `DEPLOYMENT.md`.
`ConfigError`/`BuildError` messages are shown to the operator verbatim (exit 1); usage errors exit 2.

**One tree, two channels (`rootfs.py`).** Platform files (`node/rootfs/` + generated log caps and
`update.conf`) → `common_files` → node `files` → app `files` → generated (`/etc/hostname`, `node.env`,
`.network` files, `/opt/compose/compose.yml`) are merged into one `Tree`. The same tree goes into the
Ignition config (USB) and into every full bundle, so a running node and a fresh one get changes the same
way. A path in two layers is an error, except the paths in `OVERRIDABLE`. Secrets are a separate tree
that only bundles carry — never Ignition/the USB.

**Compose.** The root `compose.yml` is generated from `node.yml` (`name:` + `include:` in app order);
parts are copied to `/opt/compose/compose.d/` by file name. Nodes never reach a registry: every service
`image:` must be listed in its app's `images`, and `build:` in a compose file is refused.

**On the node.** `watch-install.sh` polls `/opt/deploy` for `<name>.tar` + `<name>.tar.approved`, writes
the `.installed` marker *before* running the bundle's `install.sh` (one approval = one attempt), and
prunes extractions. `install.sh` verifies checksums and that the bundle is for this node, then
`update-app.sh`, then (os mode) `update-os.sh` + reboot into the other A/B partition.

## Invariants to keep

- An app bundle never reboots the node, and changed platform units are enabled but not restarted
  (restarting `compose-stack` stops the stack; restarting `watch-install` kills the running install).
- Automatic Flatcar updates stay masked (`butane.MASKED_UNITS`, `update.conf`, `disable-auto-updates.sh`);
  the only OS update path is an os bundle.
- `replace_dirs` must be at least two levels deep — checked in `config.py` and again in `update-app.sh`.
- Node scripts take every path through a `ROOT` prefix (default empty) and call `docker`,
  `docker-compose`, `systemctl`, `flatcar-update` via `PATH`. Tests run them against a scratch `ROOT`
  with the stubs in `tests/stubs/`, which append every call to `$STUB_LOG`; assertions read that log.
- Tests must keep working without internet or a docker daemon: `tests/helpers.py: Scratch` builds a
  deployment from dicts, pre-fills a fake cache (`FLATCAR_BUILDER_CACHE`) and puts the stubs on `PATH`.
- Python must run on 3.9.

## Status and docs

- `os-boot-check` (confirming the new A/B partition or falling back after an OS update) is not built
  yet; `os_boot_check.timeout` is parsed but unused. VM checks are still open. See `TASKS.md`.
- `usb-installer/` still carries IronBridge naming (`IB-` labels, GRUB entries). `usb-installer/start.sh`
  has hard-coded paths from the old repo. The maintenance shell looks for `Flatcar version:` in
  `README.txt`, which `builder/usb.py` does not write.
- User-facing docs (`README.md`, `DEPLOYMENT.md`, `usb-installer/README.md`) are in English and must
  not link to or cite `DESIGN.md` / `TASKS.md`. Those two are internal; `DESIGN.md` is in English,
  `TASKS.md` in Dutch. `TASKS.md` marks tasks `[x]` done and tested, `[~]` built with the VM check
  open, `[ ]` open.
- `DEPLOYMENT.md` documents every field of `deployment.yml` and `node.yml`; keep it in sync with `config.py`.
