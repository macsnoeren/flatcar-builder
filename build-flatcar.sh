#!/usr/bin/env bash
#
# build-flatcar.sh — entrypoint of Flatcar Builder.
#
# Works from any checkout; nothing has to be installed. It finds its own
# directory, checks what the build side needs (python3 >= 3.9 with PyYAML) and
# hands over to the Python package next to it. Docker is checked by the builder
# itself, and only when a step actually needs it, so '--check' and '--help' run
# on a machine without it.
#
#   build-flatcar.sh <deployment-dir> [--mode usb|os|app] [--node N]... [--app A]...
#                    [--out DIR] [--check]
set -euo pipefail

HERE="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

die() { printf '\033[1;31m[x] %s\033[0m\n' "$*" >&2; exit 2; }

command -v python3 >/dev/null 2>&1 \
  || die "python3 not found. Install it first (Debian/Ubuntu: apt install python3)."

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 >= 3.9 is required, found $(python3 -c 'import platform; print(platform.python_version())')."

python3 -c 'import yaml' >/dev/null 2>&1 \
  || die "PyYAML not found for $(command -v python3).
    Install it with:  apt install python3-yaml   (or: python3 -m pip install --user pyyaml)"

export PYTHONPATH="${HERE}${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m builder "$@"
