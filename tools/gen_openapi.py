"""Generate hmaps/api/openapi.json (OpenAPI 3.1) – run after changing the API."""

import json
from pathlib import Path

LatLon = {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}}
Error = {"type": "object", "properties": {"error": {"type": "object", "properties": {
    "code": {"type": "string"}, "message": {"type": "string", "description": "הודעה בעברית למשתמש"}}}}}


def q(name, desc, typ="string", required=False, **kw):
    return {"name": name, "in": "query", "required": required, "description": desc, "schema": {"type": typ, **kw}}


def p(name, desc):
    return {"name": name, "in": "path", "required": True, "description": desc, "schema": {"type": "string"}}


def ok(desc, schema=None):
    r = {"200": {"description": desc, "content": {"application/json": {"schema": schema or {"type": "object"}}}}}
    r["400"] = {"description": "פרמטר שגוי", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}
    return r


SearchResult = {"type": "object", "properties": {
    "id": {"type": "string", "examples": ["poi-123", "place-5", "street-9", "addr-9-50"]},
    "type": {"type": "string", "enum": ["poi", "place", "street", "address", "category", "coordinate", "trail"]},
    "name": {"type": "string"}, "secondary": {"type": "string"}, "category": {"type": "string"},
    "sub_category": {"type": "string"}, "icon": {"type": "string"}, "color": {"type": "string"},
    "location": {"$ref": "#/components/schemas/LatLon"}, "distance": {"type": ["integer", "null"]},
    "open_now": {"type": ["boolean", "null"]}, "kosher": {"type": ["boolean", "null"]}, "score": {"type": ["number", "null"]}}}

spec = {
    "openapi": "3.1.0",
    "info": {"title": "מפות בעברית – API", "version": "1.0.0",
             "description": "API של מערכת המפות: חיפוש בעברית, נקודות עניין, גיאוקודינג, ניווט ואריחי מפה."},
    "paths": {
        "/api/health": {"get": {"summary": "בדיקת תקינות", "responses": ok("תקין")}},
        "/api/meta": {"get": {"summary": "מידע על הנתונים והשירותים", "responses": ok("מידע")}},
        "/api/categories": {"get": {"summary": "עץ הקטגוריות", "responses": ok("קטגוריות")}},
        "/api/search/autocomplete": {"get": {"summary": "השלמה אוטומטית", "parameters": [
            q("q", "טקסט שהוקלד", required=True), q("lat", "קו רוחב להעדפת קרבה", "number"),
            q("lon", "קו אורך", "number"), q("limit", "מספר הצעות (ברירת מחדל 8)", "integer")],
            "responses": ok("הצעות", {"type": "object", "properties": {"suggestions": {"type": "array", "items": SearchResult},
                                                                       "query_time_ms": {"type": "number"}}})}},
        "/api/search": {
            "get": {"summary": "חיפוש מלא", "parameters": [
                q("q", "שאילתה, למשל 'מסעדות בתל אביב' או 'דיזנגוף 50'", required=True), q("lat", "מרכז המפה", "number"),
                q("lon", "מרכז המפה", "number"), q("user_lat", "מיקום המשתמש", "number"), q("user_lon", "מיקום המשתמש", "number"),
                q("category", "סינון לפי קטגוריה/תת-קטגוריה"), q("kosher", "רק כשר", "boolean"), q("open_now", "פתוח עכשיו", "boolean"),
                q("limit", "גודל עמוד", "integer"), q("offset", "היסט", "integer")],
                "responses": ok("תוצאות", {"type": "object", "properties": {
                    "results": {"type": "array", "items": SearchResult}, "meta": {"type": "object"},
                    "interpretation": {"type": "object", "description": "איך השאילתה פורשה (קטגוריה, כתובת, תיקון כתיב...)"}}})},
            "post": {"summary": "חיפוש מלא (גוף JSON)", "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {"query": {"type": "string"}, "location": {"$ref": "#/components/schemas/LatLon"},
                                                 "user_location": {"$ref": "#/components/schemas/LatLon"},
                                                 "filters": {"type": "object", "properties": {"category": {"type": "string"},
                                                                                              "kosher": {"type": "boolean"},
                                                                                              "open_now": {"type": "boolean"}}},
                                                 "limit": {"type": "integer"}, "offset": {"type": "integer"}}}}}},
                     "responses": ok("תוצאות")}},
        "/api/poi/{id}": {"get": {"summary": "פרטי נקודת עניין", "parameters": [p("id", "poi-123")], "responses": ok("כרטיס מידע")}},
        "/api/poi": {"get": {"summary": "נקודות עניין בסביבה", "parameters": [
            q("lat", "", "number", True), q("lon", "", "number", True), q("radius", "רדיוס במטרים (עד 50000)", "number"),
            q("category", "קטגוריה"), q("tags", "תגיות מופרדות בפסיק"), q("kosher", "", "boolean"), q("open_now", "", "boolean"),
            q("limit", "", "integer"), q("offset", "", "integer")], "responses": ok("רשימה")}},
        "/api/place/{id}": {"get": {"summary": "פרטי יישוב/שכונה כולל גבול", "parameters": [p("id", "place-5")], "responses": ok("יישוב")}},
        "/api/street/{id}": {"get": {"summary": "פרטי רחוב כולל גאומטריה", "parameters": [p("id", "street-9")], "responses": ok("רחוב")}},
        "/api/geocode": {"get": {"summary": "כתובת ← קואורדינטות", "parameters": [q("address", "כתובת", required=True), q("city", "עיר")],
                                 "responses": ok("מיקום")}},
        "/api/reverse": {"get": {"summary": "קואורדינטות ← כתובת", "parameters": [q("lat", "", "number", True), q("lon", "", "number", True)],
                                 "responses": ok("כתובת")}},
        "/api/route": {"get": {"summary": "חישוב מסלול", "parameters": [
            q("from", "lat,lon", required=True), q("to", "lat,lon", required=True), q("via", "lat,lon (ניתן לחזור)"),
            q("mode", "car | foot | bike"), q("alternatives", "מסלולים חלופיים", "boolean")], "responses": ok("מסלולים")}},
        "/api/elevation": {
            "get": {"summary": "גובה נקודות או פרופיל גובה לאורך קו (Copernicus DEM)", "parameters": [
                q("points", "lat,lon|lat,lon|… (עד 1000 נקודות)"), q("path", "קו מקודד (Google polyline, דיוק 5)"),
                q("samples", "מספר דגימות לאורך הקו (2–500, ברירת מחדל 200)", "integer")],
                "responses": {**ok("גבהים (points) או פרופיל (path)", {"type": "object", "properties": {
                    "elevations": {"type": "array", "items": {"type": "number"}},
                    "distance": {"type": "number"}, "ascent": {"type": "number"}, "descent": {"type": "number"},
                    "min": {"type": "number"}, "max": {"type": "number"},
                    "profile": {"type": "array", "description": "[מרחק במטרים, גובה, lon, lat]",
                                "items": {"type": "array", "items": {"type": "number"}}},
                    "unit": {"type": "string"}, "attribution": {"type": "string"}}}),
                    "503": {"description": "נתוני הגובה לא הותקנו"}}},
            "post": {"summary": "כמו GET, בגוף JSON (לקווים ארוכים)", "requestBody": {"content": {"application/json": {"schema": {
                "type": "object", "properties": {
                    "coordinates": {"type": "array", "description": "[[lon, lat], …] כמו ב-GeoJSON",
                                    "items": {"type": "array", "items": {"type": "number"}}},
                    "points": {"type": "array", "description": "[[lat, lon], …]", "items": {"type": "array", "items": {"type": "number"}}},
                    "samples": {"type": "integer"}}}}}},
                "responses": ok("גבהים או פרופיל")}},
        "/tiles/base.json": {"get": {"summary": "TileJSON של מפת הבסיס", "responses": ok("TileJSON")}},
        "/tiles/base/{z}/{x}/{y}.pbf": {"get": {"summary": "אריח וקטורי (OpenMapTiles)", "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                                 "responses": {"200": {"description": "MVT"}, "204": {"description": "ריק"}}}},
        "/tiles/poi/{z}/{x}/{y}.pbf": {"get": {"summary": "אריח נקודות עניין", "parameters": [p("z", ""), p("x", ""), p("y", ""),
                                                                                          q("cats", "קטגוריות מופרדות בפסיק"), q("kosher", "", "boolean")],
                                                "responses": {"200": {"description": "MVT"}}}},
        "/tiles/fallback/{z}/{x}/{y}.pbf": {"get": {"summary": "מפת בסיס חלופית מהמאגר", "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                                     "responses": {"200": {"description": "MVT"}}}},
        "/api/trails/{id}": {"get": {"summary": "שביל מסומן: שם, צבע סימון, אורך, רשת וגאומטריה", "parameters": [p("id", "trail-12")],
                                     "responses": ok("כרטיס שביל", {"type": "object", "properties": {
                                         "id": {"type": "string"}, "name": {"type": "string"}, "route": {"type": "string", "enum": ["hiking", "foot", "bicycle", "mtb"]},
                                         "network": {"type": ["string", "null"]}, "mark": {"type": "string"}, "color": {"type": "string"},
                                         "length_km": {"type": "number"}, "parts": {"type": "integer"}, "continuous": {"type": "boolean"},
                                         "bbox": {"type": "array"}, "geometry": {"type": "object", "description": "GeoJSON MultiLineString"}}})}},
        "/tiles/trails/{z}/{x}/{y}.pbf": {"get": {"summary": "אריח שבילים מסומנים (שכבה trails)", "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                                   "responses": {"200": {"description": "MVT"}, "204": {"description": "ריק"}}}},
        "/tiles/parks/{z}/{x}/{y}.pbf": {"get": {"summary": "אריח שמורות טבע, גנים לאומיים ואתרי רט\"ג (שכבה parks)",
                                                  "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                                  "responses": {"200": {"description": "MVT"}, "204": {"description": "ריק"}}}},
        "/tiles/contours/{z}/{x}/{y}.pbf": {"get": {"summary": "קווי גובה (שכבות contour: ele, idx; ‏spot: ele)",
                                                     "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                                     "responses": {"200": {"description": "MVT (gzip)"}, "204": {"description": "ריק"}}}},
        "/tiles/terrain/{z}/{x}/{y}.png": {"get": {"summary": "אריח גובה Terrarium (להצללת תבליט ולתלת-ממד)",
                                                    "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                                    "responses": {"200": {"description": "PNG"}, "404": {"description": "השכבה לא נבנתה"}}}},
        "/fonts/{fontstack}/{range}.pbf": {"get": {"summary": "גליפים לתוויות המפה", "parameters": [p("fontstack", ""), p("range", "0-255")],
                                                    "responses": {"200": {"description": "PBF"}}}},
        "/metrics": {"get": {"summary": "מדדי Prometheus", "responses": {"200": {"description": "text"}}}},
    },
    "components": {"schemas": {"LatLon": LatLon, "Error": Error, "SearchResult": SearchResult}},
}

# public transport (hmaps/api/transit.py); 503 transit_not_ready when data/transit is missing
spec["paths"].update({
    "/api/transit": {"get": {"summary": "תכנון נסיעה בתחבורה ציבורית (זמנים משוערים לפי לוח הזמנים)", "parameters": [
        q("from", "lat,lon", required=True), q("to", "lat,lon", required=True),
        q("time", "ISO 8601, שעון ישראל (ברירת מחדל: עכשיו)"), q("mode", "depart | arrive"),
        q("limit", "מספר אפשרויות (1–8)", "integer")], "responses": ok("אפשרויות נסיעה (itineraries → legs)")}},
    "/api/transit/stop/{id}": {"get": {"summary": "היציאות הקרובות מתחנה", "parameters": [
        p("id", "stop_id של GTFS, או code-<מספר התחנה>"), q("time", "ISO 8601"), q("limit", "", "integer")],
        "responses": ok("תחנה, קווים ויציאות")}},
    "/api/transit/stops": {"get": {"summary": "תחנות בסביבה", "parameters": [
        q("lat", "", "number", required=True), q("lon", "", "number", required=True), q("radius", "מטרים", "number")],
        "responses": ok("תחנות")}},
    "/api/transit/status": {"get": {"summary": "תאריכי לוח הזמנים ועדכניות הנתונים", "responses": ok("מצב")}},
})

if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "hmaps" / "api" / "openapi.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
