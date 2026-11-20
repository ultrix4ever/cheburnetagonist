#!/bin/sh
# Keeps the client default route (table 200) on a working uplink.
# Sticky: the current uplink is left only after FAILS failed checks in a row.
# No working uplink: the route is removed and clients get "unreachable" (never a direct exit).
D=/opt/amnezia/awg
INTERVAL=10
FAILS=2
cur=""; bad=0
log() { echo "$(date -u +%FT%TZ) failover: $*"; echo "$(date -u +%FT%TZ) $*" >> "$D/failover.log"; }
alive() { # uplink id -> 0 when traffic really passes through it
    [ -e "/sys/class/net/awgx$1" ] || return 1
    ping -I "awgx$1" -c 2 -W 2 -q 1.1.1.1 >/dev/null 2>&1 || ping -I "awgx$1" -c 2 -W 2 -q 8.8.8.8 >/dev/null 2>&1
}
state() { echo "$(date -u +%s) ${cur:-none}" > "$D/failover.state.tmp" && mv "$D/failover.state.tmp" "$D/failover.state"; }
order() { if [ -s "$D/exit-order" ]; then cat "$D/exit-order"; else ls "$D/exits" | sed -n 's/^awgx\([0-9]*\)\.conf$/\1/p'; fi; }
while :; do
    if [ -n "$cur" ] && alive "$cur"; then
        bad=0
    else
        [ -n "$cur" ] && bad=$((bad + 1))
        if [ -z "$cur" ] || [ "$bad" -ge "$FAILS" ]; then
            new=""
            for id in $(order); do
                [ "$id" = "$cur" ] && continue
                if alive "$id"; then new=$id; break; fi
            done
            if [ -n "$new" ]; then
                ip route replace default dev "awgx$new" table 200 metric 10
                if [ -n "$cur" ]; then
                    # Bouncing the old link drops its NAT entries, so open connections move at once.
                    ip link set "awgx$cur" down; ip link set "awgx$cur" up
                    ip route replace default dev "awgx$cur" table $((300 + cur))
                fi
                log "exit ${cur:-none} -> $new"
                cur=$new; bad=0
            elif [ -n "$cur" ]; then
                ip route del default table 200 metric 10 2>/dev/null
                log "exit $cur -> none (no working uplink, clients blocked)"
                cur=""; bad=0
            fi
        fi
    fi
    state
    if [ -f "$D/failover.log" ] && [ "$(wc -l < "$D/failover.log")" -gt 2000 ]; then tail -n 1000 "$D/failover.log" > "$D/failover.log.tmp" && mv "$D/failover.log.tmp" "$D/failover.log"; fi
    sleep "$INTERVAL"
done
