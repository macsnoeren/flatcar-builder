#!/usr/bin/env bash
#
# Runs the whole test suite: the Python tests (which also drive the node
# scripts against a scratch root) and, when shellcheck is installed, shellcheck
# over every shell script. No internet and no docker daemon needed.
#
#   bash tests/run.sh            everything
#   bash tests/run.sh -k network only tests whose name matches
set -uo pipefail
HERE="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
TOOL="$( cd "${HERE}/.." && pwd )"
rc=0

PYTHONPATH="${TOOL}" python3 -m unittest discover -s "${HERE}" -t "${TOOL}" "$@" || rc=1

if [[ $# -eq 0 ]]; then
  scripts=(build-flatcar.sh node/install.sh node/lib/*.sh node/rootfs/opt/bin/*.sh tests/run.sh)
  if command -v shellcheck >/dev/null 2>&1; then
    lint=(shellcheck)
  elif command -v docker >/dev/null 2>&1 && docker image inspect koalaman/shellcheck:stable >/dev/null 2>&1; then
    # Only an image that is already here: a test run does not download anything.
    lint=(docker run --rm -v "${TOOL}:/mnt" -w /mnt koalaman/shellcheck:stable)
  else
    lint=()
    echo "shellcheck not installed (nor the koalaman/shellcheck:stable image): shell scripts were not linted"
  fi
  if [[ "${#lint[@]}" -gt 0 ]]; then
    echo "shellcheck:"
    ( cd "${TOOL}" && "${lint[@]}" -x "${scripts[@]}" ) && echo "  clean" || rc=1
  fi
fi
exit "${rc}"
