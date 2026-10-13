#!/usr/bin/env bash
# Hebrew Maps – first-time setup / data refresh (Linux, macOS).  ./setup.sh [--update]
set -euo pipefail
cd "$(dirname "$0")"
PORT="${HMAPS_PORT:-8080}"
UPDATE=0; [[ "${1:-}" == "--update" ]] && UPDATE=1
step() { printf '\n\033[36m[%s] %s\033[0m\n' "$1" "$2"; }

command -v docker >/dev/null || { echo "Docker is required: https://docs.docker.com/get-docker/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running"; exit 1; }

step 1/6 "Building the application image"
docker compose build api
step 2/6 "Starting the web app on http://localhost:$PORT (demo data until ready)"
docker compose up -d api
step 3/6 "Downloading the Israel OpenStreetMap extract"
docker compose run --rm --user root etl sh -c "mkdir -p /data/sources /data/work /data/reports /data/tiles/build && chown 10001:10001 /data /data/sources /data/work /data/reports && chmod 777 /data/tiles/build"
docker compose run --rm etl python -m hmaps.etl download $([[ $UPDATE == 1 ]] && echo --force)
step 4/6 "Building the database"
docker compose run --rm etl python -m hmaps.etl build
docker compose run --rm etl python -m hmaps.etl gtfs $([[ $UPDATE == 1 ]] && echo --force) || echo "public transport timetables were not built (optional)"
step 5/6 "Preparing routing (background)"
if [[ $UPDATE == 1 ]]; then docker compose restart routing; else docker compose up -d routing; fi
step 6/6 "Building the basemap"
docker compose run --rm tiles
docker compose run --rm --user root etl sh -c "mv -f /data/tiles/build/israel.mbtiles /data/tiles/israel.mbtiles && chmod 644 /data/tiles/israel.mbtiles"
docker compose up -d api routing
printf '\n\033[32mDone. Open http://localhost:%s  (routing becomes available when its preparation finishes: docker compose logs -f routing)\033[0m\n' "$PORT"
