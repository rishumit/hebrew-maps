# syntax=docker/dockerfile:1
# ---------------------------------------------------------------- web vendor files
FROM node:22-alpine AS vendor
WORKDIR /v
RUN npm install --no-audit --no-fund --loglevel=error maplibre-gl@4.7.1 @mapbox/mapbox-gl-rtl-text@0.2.3 \
 && mkdir -p /out \
 && cp node_modules/maplibre-gl/dist/maplibre-gl.js node_modules/maplibre-gl/dist/maplibre-gl.css /out/ \
 && RTL="$(find node_modules/@mapbox/mapbox-gl-rtl-text -name 'mapbox-gl-rtl-text*.js' -not -path '*/src/*' | sort | head -n 1)" \
 && if [ -n "$RTL" ]; then cp "$RTL" /out/mapbox-gl-rtl-text.js; \
    else wget -q -O /out/mapbox-gl-rtl-text.js https://unpkg.com/@mapbox/mapbox-gl-rtl-text@0.2.3/mapbox-gl-rtl-text.min.js; fi \
 && test -s /out/mapbox-gl-rtl-text.js
# Hebrew UI font (optional – the UI falls back to system fonts)
RUN wget -q -O /out/Heebo.ttf "https://github.com/google/fonts/raw/main/ofl/heebo/Heebo%5Bwght%5D.ttf" || rm -f /out/Heebo.ttf
# Map label glyphs (Noto Sans, prebuilt by Protomaps). Optional: bundled Hebrew fallback glyphs are used otherwise.
RUN mkdir -p /out/fonts && ( wget -q -O /tmp/assets.zip https://github.com/protomaps/basemaps-assets/archive/refs/heads/main.zip \
      && mkdir -p /tmp/assets && unzip -q /tmp/assets.zip -d /tmp/assets \
      && cp -r /tmp/assets/basemaps-assets-main/fonts/. /out/fonts/ \
      && rm -rf /tmp/assets /tmp/assets.zip ) \
    || echo "WARNING: map fonts could not be downloaded - using the bundled Hebrew fallback glyphs"

# ---------------------------------------------------------------- application
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HMAPS_DATA=/data HMAPS_SAMPLE_DB=/app/sample/maps.db HMAPS_WEB=/app/web
RUN apt-get update \
 && apt-get install -y --no-install-recommends osmium-tool ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hmaps ./hmaps
COPY tools ./tools
COPY web ./web
COPY --from=vendor /out/ /tmp/vendor/
RUN mkdir -p web/vendor web/fonts-ui \
 && cp /tmp/vendor/maplibre-gl.js /tmp/vendor/maplibre-gl.css /tmp/vendor/mapbox-gl-rtl-text.js web/vendor/ \
 && (cp /tmp/vendor/Heebo.ttf web/fonts-ui/ 2>/dev/null || true) \
 && cp -r /tmp/vendor/fonts/. web/fonts/ \
 && rm -rf /tmp/vendor
# demo dataset baked into the image: the app works before the real data is built
RUN python tools/sample_data.py /tmp/sample >/dev/null \
 && HMAPS_DATA=/tmp/sample python -m hmaps.etl from-geojson --poi /tmp/sample/poi.geojsonseq \
      --streets /tmp/sample/streets.geojsonseq --out /app/sample/maps.db --date demo \
 && rm -rf /tmp/sample /app/sample/reports
RUN useradd --system --uid 10001 --home /app hmaps && mkdir -p /data && chown hmaps:hmaps /data
USER hmaps
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)" || exit 1
CMD ["uvicorn", "hmaps.api.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-keep-alive", "30"]
