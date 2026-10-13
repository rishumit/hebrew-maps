"""A tiny stand-in for an OSRM server (used by tests and offline demos).

Returns a straight-ish two-step route with realistic OSRM JSON structure.
"""

from __future__ import annotations

import json
import math
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _route(coords: list[tuple[float, float]], speed: float) -> dict:
    (lon1, lat1), (lon2, lat2) = coords[0], coords[-1]
    mid = (lon2, lat1)
    d1 = abs(lon2 - lon1) * 111_320 * math.cos(math.radians(lat1))
    d2 = abs(lat2 - lat1) * 110_574
    steps = [
        {"maneuver": {"type": "depart", "modifier": None, "location": [lon1, lat1], "bearing_after": 90 if lon2 > lon1 else 270},
         "name": "דיזנגוף", "ref": "", "distance": d1, "duration": d1 / speed, "mode": "driving"},
        {"maneuver": {"type": "turn", "modifier": "left", "location": list(mid)}, "name": "שדרות רוטשילד", "distance": d2,
         "duration": d2 / speed},
        {"maneuver": {"type": "roundabout", "modifier": "right", "exit": 2, "location": [lon2, lat2 - 0.0005]}, "name": "אלנבי",
         "distance": 50, "duration": 10},
        {"maneuver": {"type": "arrive", "modifier": "right", "location": [lon2, lat2]}, "name": "", "distance": 0, "duration": 0},
    ]
    return {"distance": d1 + d2 + 50, "duration": (d1 + d2) / speed + 10,
            "geometry": {"type": "LineString", "coordinates": [[lon1, lat1], list(mid), [lon2, lat2]]},
            "legs": [{"steps": steps}]}


class Handler(BaseHTTPRequestHandler):
    speed = 10.0

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path.startswith("/nearest/"):
            body = {"code": "Ok", "waypoints": []}
        elif path.startswith("/route/"):
            coord_str = path.split("/")[-1]
            coords = [tuple(map(float, c.split(","))) for c in coord_str.split(";")]
            if any(abs(c[0] - 35.0) < 1e-9 and abs(c[1] - 29.0) < 1e-9 for c in coords):
                body = {"code": "NoSegment", "message": "Could not find a matching segment"}
            else:
                body = {"code": "Ok", "routes": [_route(coords, self.speed)], "waypoints": []}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ports = [int(p) for p in sys.argv[1:]] or [5001, 5002, 5003]
    for p in ports:
        serve(p)
    print("fake OSRM on", ports)
    threading.Event().wait()
