#!/bin/sh
# OSRM routing for car / foot / bicycle.
# Prepares the routing graphs from the Israel OSM extract (only when missing or
# when the extract is newer), then serves the three profiles on ports 5001-5003.
set -eu
PBF="${PBF:-/data/sources/israel-and-palestine.osm.pbf}"
OUT=/data/routing
mkdir -p "$OUT"

log() { echo "[routing] $(date '+%H:%M:%S') $*"; }

while [ ! -s "$PBF" ]; do
  log "waiting for the OSM extract ($PBF) – it is downloaded by the setup script"
  sleep 30
done

prepare() {
  profile="$1"; dir="$OUT/$profile"
  if [ -f "$dir/.built" ] && [ ! "$PBF" -nt "$dir/.built" ]; then
    log "$profile: graph is up to date"
    return
  fi
  log "$profile: preparing routing graph (takes a few minutes)…"
  rm -rf "$dir.tmp"
  mkdir -p "$dir.tmp"
  cp "$PBF" "$dir.tmp/israel.osm.pbf"
  osrm-extract -p "/opt/$profile.lua" "$dir.tmp/israel.osm.pbf"
  osrm-partition "$dir.tmp/israel.osrm"
  osrm-customize "$dir.tmp/israel.osrm"
  rm -f "$dir.tmp/israel.osm.pbf"
  touch "$dir.tmp/.built"
  rm -rf "$dir"
  mv "$dir.tmp" "$dir"
  log "$profile: ready"
}

prepare car
prepare foot
prepare bicycle

log "starting routing servers"
osrm-routed --algorithm mld --port 5001 --max-table-size 1000 "$OUT/car/israel.osrm" &
P1=$!
osrm-routed --algorithm mld --port 5002 "$OUT/foot/israel.osrm" &
P2=$!
osrm-routed --algorithm mld --port 5003 "$OUT/bicycle/israel.osrm" &
P3=$!
# exit (and let Docker restart us) as soon as one server dies
while kill -0 "$P1" 2>/dev/null && kill -0 "$P2" 2>/dev/null && kill -0 "$P3" 2>/dev/null; do
  sleep 15
done
log "a routing server stopped – restarting container"
exit 1
