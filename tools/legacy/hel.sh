#!/bin/bash
# Current Gateway uses the pinned vpn-helsinki alias in /root/.ssh/config.
# The old 192.0.2.20 jump and its project key were removed on 2026-11-14.
# Usage: hel.sh 'command' (or hel.sh without arguments for an interactive session).
exec ssh -o BatchMode=yes vpn-helsinki "$@"
