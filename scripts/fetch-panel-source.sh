#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
checkout="$root/.cache/upstream/amnezia-control"
revision=7e0598b950b86f24dfdcff392bd5d8eccdb74f6e
if [ ! -d "$checkout/.git" ]; then
  git clone --no-checkout https://github.com/MihSergeev/amnezia-control.git "$checkout"
fi
git -C "$checkout" fetch origin "$revision"
git -C "$checkout" checkout --detach "$revision"
test "$(git -C "$checkout" rev-parse HEAD)" = "$revision"
printf 'Pinned upstream ready: %s\n' "$revision"
