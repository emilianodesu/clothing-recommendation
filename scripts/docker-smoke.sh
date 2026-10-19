#!/usr/bin/env bash
# Exercise an isolated Compose project, then remove only its own test resources.
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v curl >/dev/null
docker compose version
docker buildx version
if [[ -n "${DOCKER_HOST:-}" ]]; then
  echo 'Usa un contexto local de Docker y elimina DOCKER_HOST de esta terminal.' >&2
  exit 1
fi
endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
case "$endpoint" in
  unix://*) ;;
  *) echo 'Esta prueba sólo admite un motor local con socket Unix.' >&2; exit 1 ;;
esac
project="hm-docker-smoke-$(date +%s)-$$"
export SMOKE_IMAGE="$project:test"
compose() { docker compose --project-name "$project" --file "$task_root/docker/smoke/compose.yaml" "$@"; }
cleanup() {
  result=$?
  trap - EXIT
  set +e
  compose down --volumes --remove-orphans
  cleanup_result=$?
  docker image rm "$SMOKE_IMAGE" >/dev/null 2>&1
  if [[ $result -eq 0 && $cleanup_result -ne 0 ]]; then result=$cleanup_result; fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
compose config --quiet
compose up --build --detach --wait --wait-timeout 60 web
address="$(compose port web 8080)"
[[ "$(curl --noproxy '*' --fail --silent --show-error --max-time 10 "http://$address")" == 'docker-ready' ]]
echo 'PASS: build, healthcheck y HTTP desde el host.'
[[ "$(compose run --rm --no-deps -T client)" == 'docker-ready' ]]
echo 'PASS: DNS y comunicación entre contenedores.'
compose exec -T web sh -ec 'test "$(cat /fixture.txt)" = "mount-ok"; printf "%s\n" "$1" > /state/probe' sh "$project"
compose up --detach --force-recreate --wait --wait-timeout 60 web
[[ "$(compose exec -T web cat /state/probe)" == "$project" ]]
echo 'PASS: montaje del proyecto y volumen persistente tras recrear el contenedor.'
echo 'Prueba Docker/Compose completada; retirando exclusivamente los recursos temporales.'
