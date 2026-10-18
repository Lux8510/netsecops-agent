#!/usr/bin/env bash
set -Eeuo pipefail

lab_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
topology_file="$lab_dir/topology.clab.yml"
results_dir="$lab_dir/verification-results"
result_file="$results_dir/verification-$(date -u +%Y%m%dT%H%M%SZ).txt"

lab_name="netsecops-ospf"
r1_container="clab-${lab_name}-r1"
r2_container="clab-${lab_name}-r2"
expected_image="quay.io/frrouting/frr:containerlab-10.7.1"

log() {
  printf '\n==> %s\n' "$1"
}

die() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

for required_command in git python3 docker containerlab; do
  command -v "$required_command" >/dev/null 2>&1 || \
    die "Missing required command: $required_command. No installation was attempted."
done

if [[ "$(uname -r)" != *microsoft-standard-WSL2* ]]; then
  die "This experiment must run inside WSL2."
fi

mkdir -p "$results_dir"
exec > >(tee -a "$result_file") 2>&1

log "Environment"
printf 'timestamp_utc=%s\n' "$(date -u -Iseconds)"
printf 'workspace=%s\n' "$lab_dir"
uname -r
git --version
python3 --version
sudo -v
sudo docker version --format 'docker client={{.Client.Version}} server={{.Server.Version}}'
containerlab version

container_exists() {
  sudo docker inspect "$1" >/dev/null 2>&1
}

container_is_running() {
  [[ "$(sudo docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" == "true" ]]
}

log "Collision check"
r1_exists=false
r2_exists=false
container_exists "$r1_container" && r1_exists=true
container_exists "$r2_container" && r2_exists=true

if [[ "$r1_exists" == false && "$r2_exists" == false ]]; then
  log "Deploying the lab (no destroy or cleanup will be run)"
  sudo containerlab deploy -t "$topology_file"
elif [[ "$r1_exists" == true && "$r2_exists" == true ]] && \
     container_is_running "$r1_container" && container_is_running "$r2_container"; then
  log "Both expected containers already exist and are running; reusing them"
else
  die "A partial or stopped lab with the same names exists. Nothing was deleted or reset. Inspect it manually before retrying."
fi

log "Container status"
for container_name in "$r1_container" "$r2_container"; do
  container_is_running "$container_name" || die "$container_name is not running."
  actual_image="$(sudo docker inspect --format '{{.Config.Image}}' "$container_name")"
  [[ "$actual_image" == "$expected_image" ]] || \
    die "$container_name uses unexpected image $actual_image. Nothing was changed."
  sudo docker inspect --format 'name={{.Name}} status={{.State.Status}} image={{.Config.Image}}' "$container_name"
done
sudo containerlab inspect -t "$topology_file"

ensure_ospf_networks() {
  local container_name="$1"
  local loopback_network="$2"
  local running_config

  running_config="$(sudo docker exec "$container_name" vtysh -c 'show running-config')"
  if grep -q '^[[:space:]]*ip ospf area ' <<<"$running_config"; then
    sudo docker exec "$container_name" vtysh \
      -c 'configure terminal' \
      -c 'interface eth1' \
      -c 'no ip ospf area 0.0.0.0' \
      -c 'exit' \
      -c 'interface lo' \
      -c 'no ip ospf area 0.0.0.0' \
      -c 'end'
  fi

  sudo docker exec "$container_name" vtysh \
    -c 'configure terminal' \
    -c 'router ospf' \
    -c 'network 10.0.12.0/30 area 0.0.0.0' \
    -c "network $loopback_network area 0.0.0.0" \
    -c 'end'
}

log "Ensuring the intended OSPF networks are active in the running lab"
ensure_ospf_networks "$r1_container" '10.255.0.1/32'
ensure_ospf_networks "$r2_container" '10.255.0.2/32'

ospf_full() {
  local container_name="$1"
  local expected_router_id="$2"
  local neighbor_output
  neighbor_output="$(sudo docker exec "$container_name" vtysh -c 'show ip ospf neighbor' 2>&1)" || return 1
  grep -q "$expected_router_id" <<<"$neighbor_output" && grep -q 'Full' <<<"$neighbor_output"
}

log "Waiting for OSPF neighbors to reach Full"
ospf_ready=false
for _ in $(seq 1 30); do
  if ospf_full "$r1_container" '10.255.0.2' && ospf_full "$r2_container" '10.255.0.1'; then
    ospf_ready=true
    break
  fi
  sleep 2
done

if [[ "$ospf_ready" != true ]]; then
  sudo docker exec "$r1_container" vtysh -c 'show ip ospf neighbor' || true
  sudo docker exec "$r2_container" vtysh -c 'show ip ospf neighbor' || true
  die "OSPF neighbors did not reach Full within 60 seconds. The lab was left running for diagnosis."
fi

ospf_route_present() {
  local container_name="$1"
  local expected_prefix="$2"
  sudo docker exec "$container_name" vtysh -c 'show ip route ospf' 2>&1 | \
    grep -q "$expected_prefix"
}

log "Waiting for both remote loopback routes"
routes_ready=false
for _ in $(seq 1 15); do
  if ospf_route_present "$r1_container" '10.255.0.2/32' && \
     ospf_route_present "$r2_container" '10.255.0.1/32'; then
    routes_ready=true
    break
  fi
  sleep 2
done

if [[ "$routes_ready" != true ]]; then
  sudo docker exec "$r1_container" vtysh -c 'show ip route ospf' || true
  sudo docker exec "$r2_container" vtysh -c 'show ip route ospf' || true
  die "Remote loopback routes were not learned within 30 seconds. The lab was left running for diagnosis."
fi

log "r1 OSPF neighbors"
sudo docker exec "$r1_container" vtysh -c 'show ip ospf neighbor'

log "r2 OSPF neighbors"
sudo docker exec "$r2_container" vtysh -c 'show ip ospf neighbor'

log "r1 OSPF routes"
sudo docker exec "$r1_container" vtysh -c 'show ip route ospf'

log "r2 OSPF routes"
sudo docker exec "$r2_container" vtysh -c 'show ip route ospf'

log "Ping r1 loopback to r2 loopback"
ping_failed=false
sudo docker exec "$r1_container" ping -c 3 -W 2 10.255.0.2 || ping_failed=true

log "Ping r2 loopback to r1 loopback"
sudo docker exec "$r2_container" ping -c 3 -W 2 10.255.0.1 || ping_failed=true

if [[ "$ping_failed" == true ]]; then
  die "At least one loopback ping failed. The lab was left running for diagnosis."
fi

log "PASS"
printf 'Both containers are running, both OSPF adjacencies are Full, and both loopback pings succeeded.\n'
printf 'The lab remains deployed. Verification output: %s\n' "$result_file"
