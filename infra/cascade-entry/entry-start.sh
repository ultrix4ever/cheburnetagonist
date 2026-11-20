#!/bin/sh
# Shared cascade entry: clients on awg0 leave only through one of the awgx<N> uplinks.
set -eu
D=/opt/amnezia/awg
NET=$(sed -n 's/^Address *= *//p' "$D/awg0.conf" | head -1)
# Fail closed before the client-facing interface exists.
iptables -I FORWARD 1 -i awg0 ! -o awgx+ -j DROP
ip6tables -I FORWARD 1 -i awg0 -j DROP
ip rule add pref 1100 iif awg0 table 200
ip rule add pref 1101 iif awg0 unreachable
ip route add unreachable default table 200 metric 32760
awg-quick up "$D/awg0.conf"
ip link set awg0 mtu 1280
for f in "$D"/exits/awgx*.conf; do
    [ -e "$f" ] || continue
    n=$(basename "$f" .conf); id=${n#awgx}
    # One broken uplink must not stop the entry; the selector skips missing interfaces.
    if awg-quick up "$f"; then
        ip route add default dev "$n" table $((300 + id)) || true
        ip rule add pref $((900 + id)) oif "$n" table $((300 + id)) || true
    fi
done
iptables -t nat -A POSTROUTING -s "$NET" -o awgx+ -j MASQUERADE
iptables -t mangle -A FORWARD -i awg0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
"$D/failover.sh" &
trap 'exit 0' TERM INT
while :; do sleep 3600 & wait $!; done
