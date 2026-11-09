#!/usr/bin/env bash
# Reserved lab resources: eln-ecu, eln-gw, eln-e0, eln-g0.
set -euo pipefail

die() { echo "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || die 'Run this lab inside Linux (Ubuntu VM or WSL2).'
[[ $EUID == 0 ]] || die "Run: sudo bash scripts/lab.sh ${1:-status}"
command -v ip >/dev/null || die 'Install iproute2 first.'

exists() { ip netns list | awk '{print $1}' | grep -Fxq "$1"; }
require_pair() {
    exists eln-ecu && exists eln-gw || die 'Run up first.'
}

case "${1:-status}" in
    up)
        for ns in eln-ecu eln-gw; do
            if exists "$ns"; then die "$ns already exists; inspect with status first."; fi
        done
        for dev in eln-e0 eln-g0; do
            if ip link show dev "$dev" &>/dev/null; then die "$dev already exists."; fi
        done
        made_ecu=0; made_gw=0; made_link=0
        rollback() {
            if (( made_link )); then ip link del eln-e0 2>/dev/null || true; fi
            if (( made_ecu )); then ip netns del eln-ecu || true; fi
            if (( made_gw )); then ip netns del eln-gw || true; fi
        }
        trap rollback ERR
        ip netns add eln-ecu
        made_ecu=1
        ip netns add eln-gw
        made_gw=1
        ip link add eln-e0 type veth peer name eln-g0
        made_link=1
        ip link set eln-e0 netns eln-ecu
        ip link set eln-g0 netns eln-gw
        ip -n eln-ecu addr add 10.22.0.1/24 dev eln-e0
        ip -n eln-gw addr add 10.22.0.2/24 dev eln-g0
        ip -n eln-ecu link set lo up
        ip -n eln-gw link set lo up
        ip -n eln-ecu link set eln-e0 up
        ip -n eln-gw link set eln-g0 up
        trap - ERR
        echo 'Ready: ECU 10.22.0.1/24 <--- veth ---> Gateway 10.22.0.2/24'
        ;;
    down)
        # Avoid leaving running processes holding anonymous namespaces alive.
        for ns in eln-ecu eln-gw; do
            if exists "$ns"; then
                [[ -z $(ip netns pids "$ns") ]] || die "Stop all processes in $ns before down."
            fi
        done
        for ns in eln-ecu eln-gw; do
            if exists "$ns"; then ip netns del "$ns"; fi
        done
        echo 'Lab namespaces removed.'
        ;;
    ping)
        require_pair
        ip netns exec eln-ecu ping -c 4 -W 2 10.22.0.2
        ;;
    delay)
        require_pair
        command -v tc >/dev/null || die 'Install iproute2 first.'
        # Only ECU egress: this is not 50 ms of delay in both directions.
        ip netns exec eln-ecu tc qdisc replace dev eln-e0 root netem delay 50ms loss 1%
        echo 'ECU egress: 50 ms delay, 1% random loss.'
        ;;
    clear)
        require_pair
        current=$(ip netns exec eln-ecu tc qdisc show dev eln-e0)
        if [[ $current == *'qdisc netem '* ]]; then
            ip netns exec eln-ecu tc qdisc del dev eln-e0 root
        fi
        ;;
    status)
        for ns in eln-ecu eln-gw; do
            if exists "$ns"; then
                echo "[$ns]"
                ip -n "$ns" -br addr
                ip -n "$ns" route
                ip -n "$ns" neigh
            else
                echo "$ns: absent"
            fi
        done
        ;;
    *) die 'Usage: sudo bash scripts/lab.sh {up|down|status|ping|delay|clear}' ;;
esac
