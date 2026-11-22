#!/bin/sh
# Keeps the client default route (table 200) on a working uplink.
# exit-order: preferred exits, one node id per line, best first.
# exit-last:  last-resort exits, used only while no preferred exit works.
# Sticky inside the preferred list: the current uplink is left only after FAILS failed checks in a row.
# On a last-resort exit the preferred ones are re-tested, and clients move back once one of them
# passes RETURN_OK loss-free tests in a row.
# No working uplink: the route is removed and clients get "unreachable" (never a direct exit).
D=/opt/amnezia/awg
INTERVAL=10
FAILS=2
RETURN_EVERY=3
RETURN_OK=3
log() { echo "$(date -u +%FT%TZ) failover: $*"; echo "$(date -u +%FT%TZ) $*" >> "$D/failover.log"; }
alive() { # uplink id -> 0 when traffic really passes through it
    [ -e "/sys/class/net/awgx$1" ] || return 1
    ping -I "awgx$1" -c 2 -W 2 -q 1.1.1.1 >/dev/null 2>&1 || ping -I "awgx$1" -c 2 -W 2 -q 8.8.8.8 >/dev/null 2>&1
}
solid() { # stricter: four replies out of four
    [ -e "/sys/class/net/awgx$1" ] || return 1
    ping -I "awgx$1" -c 4 -W 2 -q 1.1.1.1 2>/dev/null | grep -q ' 0% packet loss'
}
state() { echo "$(date -u +%s) ${cur:-none}" > "$D/failover.state.tmp" && mv "$D/failover.state.tmp" "$D/failover.state"; }
ids() { [ -s "$1" ] && grep -E '^[0-9]+$' "$1"; }
is_last() { ids "$D/exit-last" | grep -qx "$1"; }
preferred() {
    if [ -s "$D/exit-order" ]; then ids "$D/exit-order"; else ls "$D/exits" | sed -n 's/^awgx\([0-9]*\)\.conf$/\1/p'; fi |
        while read -r id; do is_last "$id" || echo "$id"; done
}
switch() { # new id, reason
    ip route replace default dev "awgx$1" table 200 metric 10
    if [ -n "$cur" ]; then
        # Bouncing the old link drops its NAT entries, so open connections move at once.
        ip link set "awgx$cur" down; ip link set "awgx$cur" up
        ip route replace default dev "awgx$cur" table $((300 + cur))
    fi
    log "exit ${cur:-none} -> $1${2:+ ($2)}"
    cur=$1; bad=0; cand=""; good=0
}
# A restarted selector continues from the route that is already serving clients.
cur=$(ip route show table 200 | sed -n 's/^default dev awgx\([0-9]*\) .*/\1/p' | head -1)
bad=0; cand=""; good=0; tick=0
while :; do
    tick=$((tick + 1))
    if [ -n "$cur" ] && alive "$cur"; then
        bad=0
        if is_last "$cur" && [ $((tick % RETURN_EVERY)) -eq 0 ]; then
            best=""
            for id in $(preferred); do if solid "$id"; then best=$id; break; fi; done
            if [ -n "$best" ] && [ "$best" = "$cand" ]; then good=$((good + 1)); else cand=$best; good=${best:+1}; good=${good:-0}; fi
            [ -n "$cand" ] && [ "$good" -ge "$RETURN_OK" ] && switch "$cand" "preferred exit is back"
        fi
    else
        [ -n "$cur" ] && bad=$((bad + 1))
        if [ -z "$cur" ] || [ "$bad" -ge "$FAILS" ]; then
            new=""
            for id in $(preferred) $(ids "$D/exit-last"); do
                [ "$id" = "$cur" ] && continue
                if alive "$id"; then new=$id; break; fi
            done
            if [ -n "$new" ]; then
                switch "$new"
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
