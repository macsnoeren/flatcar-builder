#!/usr/bin/env bash
# health-check.sh — is this node's stack up?
#
# Fails when compose-stack.service is not active, or when any container of the
# stack is not running. A container that has exited with code 0 is fine: that
# is a one-shot init container that did its job.
#
# Exit 0 = healthy. Prints one FAIL line per problem.
set -u

ROOT="${ROOT:-}"
COMPOSE_FILE="${ROOT}/opt/compose/compose.yml"
DC="${DOCKER_COMPOSE:-${ROOT}/opt/bin/docker-compose}"
EXIT=0

if [[ -f "${ROOT}/etc/flatcar-builder/node.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  . "${ROOT}/etc/flatcar-builder/node.env"
  set +a
fi

systemctl is-active --quiet compose-stack.service \
  || { echo "FAIL compose-stack.service is not active"; EXIT=1; }

if [[ -x "${DC}" && -f "${COMPOSE_FILE}" ]]; then
  # -a: without it 'ps' leaves out exactly the containers that are not running.
  while IFS=$'\t' read -r name state code; do
    [[ -n "${name}" ]] || continue
    case "${state}" in
      running) ;;
      exited) [[ "${code}" == "0" ]] || { echo "FAIL ${name} exited with code ${code}"; EXIT=1; } ;;
      *) echo "FAIL ${name} (${state})"; EXIT=1 ;;
    esac
  done < <("${DC}" -f "${COMPOSE_FILE}" ps -a --format '{{.Name}}	{{.State}}	{{.ExitCode}}' 2>/dev/null)
else
  echo "FAIL ${DC} or ${COMPOSE_FILE} missing"
  EXIT=1
fi

[[ "${EXIT}" -eq 0 ]] && echo "OK"
exit "${EXIT}"
