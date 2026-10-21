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


Photo = {"type": "object", "description": "תמונה עם קרדיט (חובה להציג: צלם, רישיון, קישור למקור)", "properties": {
    "src": {"type": "string"}, "full": {"type": "string"}, "title": {"type": ["string", "null"]},
    "author": {"type": ["string", "null"]}, "license": {"type": ["string", "null"]}, "license_url": {"type": ["string", "null"]},
    "page_url": {"type": ["string", "null"]}, "source": {"type": "string", "enum": ["commons", "mapillary", "web"]}}}
LongText = {"type": "object", "properties": {
    "source": {"type": "string", "enum": ["wikipedia", "wikivoyage"]}, "title": {"type": "string"}, "text": {"type": "string"},
    "paragraphs": {"type": "array", "items": {"type": "string"}}, "url": {"type": "string"}, "license": {"type": "string"}}}
RICH = {
    "media": {"type": "object", "properties": {"gallery": {"type": "array", "items": Photo}}},
    "texts": {"type": "array", "items": LongText},
}

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
        "/api/poi/{id}": {"get": {"summary": "פרטי נקודת עניין (באתרי טיול: גלריה, טקסט מוויקיפדיה / ויקימסע, עובדות, שבילים ואתרים בסביבה)",
                                  "parameters": [p("id", "poi-123")], "responses": ok("כרטיס מידע", {"type": "object", "properties": {
                                      **RICH,
                                      "facts": {"type": "array", "items": {"type": "object", "properties": {
                                          "label": {"type": "string"}, "value": {"type": "string"}, "icon": {"type": "string"}}}},
                                      "nearby_trails": {"type": "array", "items": {"type": "object"}},
                                      "nearby_sites": {"type": "array", "items": {"type": "object"}},
                                      "official_website": {"type": "string"}, "commons_category_url": {"type": "string"}}})}},
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
                                         "bbox": {"type": "array"}, "geometry": {"type": "object", "description": "GeoJSON MultiLineString"},
                                         **RICH,
                                         "start": LatLon, "end": LatLon, "loop": {"type": "boolean"},
                                         "duration_min": {"type": ["integer", "null"], "description": "זמן משוער ללא עליות"},
                                         "difficulty_he": {"type": ["string", "null"]},
                                         "sites_along": {"type": "array", "items": {"type": "object"},
                                                         "description": "אתרים, מעיינות, ברזיות ותצפיות עד 150 מ׳ מהשביל, לפי סדר ההליכה"}}})}},
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
        "/media/{key}": {"get": {"summary": "עותק מקומי של תמונה (tools/media.py)", "parameters": [p("key", "24 תווי hex")],
                                 "responses": {"200": {"description": "image"}, "404": {"description": "לא הורד"}}}},
        "/media/mly/{id}": {"get": {"summary": "תמונת Mapillary (הפניה לכתובת עדכנית, דורש מפתח בשרת)", "parameters": [p("id", "")],
                                    "responses": {"302": {"description": "הפניה"}, "404": {"description": "אין מפתח / לא נמצא"}}}},
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
    "/api/transit/stop/{id}/routes": {"get": {"summary": "כל הקווים שעוברים בתחנה, לפי סוג תחבורה וממוינים לפי מספר הקו",
        "parameters": [p("id", "stop_id של GTFS, או code-<מספר התחנה>")],
        "responses": {**ok("תחנה וקבוצות קווים", {"type": "object", "properties": {
            "stop": {"type": "object"}, "count": {"type": "integer"},
            "groups": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"type": "string"}, "mode_he": {"type": "string"},
                "lines": {"type": "array", "items": {"$ref": "#/components/schemas/TransitLine"}}}}}}}),
            "404": {"description": "התחנה לא נמצאה"}}}},
    "/api/transit/route/{id}": {"get": {"summary": "קו: רשימת התחנות המלאה של כיוון אחד (התבנית הנפוצה), המסלול והכיוונים",
        "parameters": [p("id", "route_id של GTFS (route_id מהיציאות / מקטעי הנסיעה / קווי התחנה)"),
                       q("direction", "מספר הכיוון ברשימת directions (ברירת מחדל: הכיוון שעוצר ב-from_stop)", "integer"),
                       q("from_stop", "stop_id של התחנה שממנה הגיעו (מסומנת ברשימה)")],
        "responses": {**ok("קו", {"type": "object", "properties": {
            "route": {"type": "object"}, "direction": {"type": "integer"}, "headsign": {"type": "string"},
            "directions": {"type": "array", "items": {"type": "object", "properties": {
                "route_id": {"type": "string"}, "direction": {"type": "integer"}, "headsign": {"type": "string"},
                "from": {"type": "string"}, "to": {"type": "string"}, "num_stops": {"type": "integer"},
                "serves_stop": {"type": "boolean"}, "current": {"type": "boolean"}}}},
            "from_index": {"type": ["integer", "null"]},
            "stops": {"type": "array", "items": {"type": "object", "description": "תחנה + offset (שניות מתחילת הנסיעה), pickup, drop_off"}},
            "geometry": {"type": "object", "description": "GeoJSON LineString (shapes.txt, או קו בין התחנות)"}}}),
            "404": {"description": "הקו או התחנה לא נמצאו"}}}},
    "/api/transit/stops": {"get": {"summary": "תחנות בסביבה", "parameters": [
        q("lat", "", "number", required=True), q("lon", "", "number", required=True), q("radius", "מטרים", "number"),
        q("mode", "רק תחנות של אמצעי תחבורה זה (bus, rail, light_rail…)"),
        q("code", "מספר התחנה שעל השלט (ref של תחנה ב-OSM) – תחנה תואמת מוחזרת ראשונה עם match=code", "integer")],
        "responses": ok("תחנות (כל אחת עם modes – אמצעי התחבורה העוצרים בה)")}},
    "/api/transit/status": {"get": {"summary": "תאריכי לוח הזמנים ועדכניות הנתונים", "responses": ok("מצב")}},
})

spec["components"]["schemas"]["TransitLine"] = {"type": "object", "properties": {
    "id": {"type": "string", "description": "route_id המייצג (הנפוץ בתחנה)"}, "line": {"type": "string"},
    "route_short_name": {"type": ["string", "null"]}, "type": {"type": "string"}, "mode_he": {"type": "string"},
    "agency": {"type": "string"}, "color": {"type": "string"}, "text_color": {"type": "string"},
    "route_ids": {"type": "array", "items": {"type": "string"}, "description": "כל ה-route_id של הקו (כיוונים וחלופות)"},
    "terminates": {"type": "boolean", "description": "הקו רק מסיים בתחנה (אין עלייה)"},
    "directions": {"type": "array", "items": {"type": "object", "properties": {
        "route_id": {"type": "string"}, "direction": {"type": "integer"}, "direction_id": {"type": ["integer", "null"]},
        "headsign": {"type": "string"}, "trips": {"type": "integer"}}}}}}

# synagogue prayer times (hmaps/api/minyanim.py, data: hmaps/etl/zmanim.py – Nedarim Plus)
Minyan = {"type": "object", "properties": {
    "prayer": {"type": "string", "examples": ["שחרית", "מנחה", "ערבית", "שיעור"]}, "time": {"type": "string", "examples": ["06:30"]},
    "days": {"type": "string", "description": "אותיות הימים: א..ו, ש (שבת)", "examples": ["אבגדה"]},
    "hall": {"type": ["string", "null"], "description": "אולם / חדר בתוך הבניין"}, "note": {"type": ["string", "null"]}}}
spec["paths"]["/api/poi/{id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["minyanim"] = {
    "type": "object", "description": "זמני תפילות (בתי כנסת המקושרים למאגר נדרים פלוס בלבד)", "properties": {
        "source": {"type": "string"}, "source_url": {"type": "string"}, "mosad_id": {"type": "string"},
        "name": {"type": ["string", "null"]}, "address": {"type": ["string", "null"]},
        "phone": {"type": ["string", "null"], "description": "קו טלפוני לשמיעת הזמנים"},
        "updated": {"type": "string", "description": "תאריך משיכת הנתונים"},
        "items": {"type": "array", "items": Minyan},
        "sun": {"type": ["object", "null"], "description": "זריחה / שקיעה להיום בקירוב (גובה פני הים)",
                "properties": {"sunrise": {"type": "string"}, "sunset": {"type": "string"}}},
        "sun_note": {"type": "string"}}}
spec["paths"]["/api/minyanim/near"] = {"get": {
    "summary": "מניינים קרובים: התפילות הבאות בסביבה (24 השעות הקרובות), לפי שעה ואז מרחק", "parameters": [
        q("lat", "", "number", True), q("lon", "", "number", True), q("radius", "מטרים (50–10000, ברירת מחדל 1000)", "number"),
        q("prayer", "שחרית | מנחה | ערבית | שיעור (ברירת מחדל: כל התפילות, בלי שיעורים)"),
        q("after", "HH:MM (ברירת מחדל: עכשיו, שעון ישראל)"), q("day", "א..ש או 0..6 (0 = ראשון; ברירת מחדל: היום)"),
        q("limit", "", "integer")],
    "responses": ok("מניינים", {"type": "object", "properties": {"results": {"type": "array", "items": {"allOf": [Minyan, {
        "type": "object", "properties": {
            "mosad_id": {"type": "string"}, "poi_id": {"type": ["string", "null"], "examples": ["poi-123"]},
            "name": {"type": "string"}, "address": {"type": ["string", "null"]}, "lat": {"type": "number"},
            "lon": {"type": "number"}, "distance_m": {"type": "integer"},
            "tomorrow": {"type": "boolean", "description": "המניין ביום שאחרי"}}}]}},
        "meta": {"type": "object"}}})}}
# hiking-route guide (hmaps/api/guide.py)
Stage = {"type": "object", "properties": {
    "n": {"type": "integer"}, "from": {"type": "object"}, "to": {"type": "object", "description": "kind: waypoint | junction | sight | distance"},
    "distance_m": {"type": "integer"}, "duration_min": {"type": "integer"}, "ascent": {"type": ["integer", "null"]},
    "descent": {"type": ["integer", "null"]}, "marks": {"type": "array", "items": {"type": "object"}}, "stream": {"type": ["string", "null"]},
    "sights": {"type": "array", "items": {"type": "object"}}, "instruction": {"type": "string", "description": "הוראה בעברית"},
    "bbox": {"type": "array"}, "geometry": {"type": "object"}}}
Guide = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": ["hike", "trail", "route"]}, "ref": {"type": ["string", "null"]}, "name": {"type": "string"},
    "summary": {"type": "object", "description": "אורך, זמן (time_formula: tobler | naismith | flat), עלייה/ירידה, גבהים, קושי, מעגלי, סימון שבילים"},
    "start": {"type": "object"}, "end": {"type": "object"}, "stages": {"type": "array", "items": Stage},
    "sights": {"type": "array", "items": {"type": "object"}}, "elevation": {"type": ["object", "null"]},
    "practical": {"type": "object", "description": "מים, שירותים, חניה, צל, שעות אור (שקיעה), חירום, טיפים"},
    "credits": {"type": "array", "items": {"type": "object"}}, "geometry": {"type": "object"}, "bbox": {"type": "array"}}}
spec["paths"].update({
    "/api/hikes": {"get": {"summary": "מסלולי טיול מתוארים באזור", "parameters": [
        q("bbox", "minlon,minlat,maxlon,maxlat"), q("lat", "", "number"), q("lon", "", "number"),
        q("radius", "מטרים (ברירת מחדל 20000)", "number"), q("limit", "", "integer"), q("geometry", "1 = כולל קו מפושט", "boolean")],
        "responses": ok("מסלולים", {"type": "object", "properties": {"results": {"type": "array", "items": {"type": "object"}}}})}},
    "/api/hikes/{id}": {"get": {"summary": "כרטיס מסלול טיול (תיאור, נקודות ציון, טיפים, גאומטריה)", "parameters": [p("id", "hike-12")],
                                "responses": ok("מסלול")}},
    "/api/hikes/{id}/guide": {"get": {"summary": "מדריך מסלול: סיכום, שלבים, אתרים, מידע מעשי ובטיחות",
                                      "parameters": [p("id", "hike-12"), q("date", "YYYY-MM-DD לחישוב שעות האור")],
                                      "responses": ok("מדריך", Guide)}},
    "/api/trails/{id}/guide": {"get": {"summary": "מדריך מסלול לשביל מסומן", "parameters": [p("id", "trail-12"), q("date", "YYYY-MM-DD")],
                                       "responses": ok("מדריך", Guide)}},
    "/api/guide": {"post": {"summary": "מדריך מסלול לקו כלשהו (למשל מסלול הליכה שתוכנן)",
                            "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                                "coordinates": {"type": "array", "description": "[[lon, lat], …] מההתחלה לסוף"},
                                "name": {"type": "string"}, "waypoints": {"type": "array"}, "date": {"type": "string"}}}}}},
                            "responses": {**ok("מדריך", Guide), "422": {"description": "המסלול ארוך מדי / לא תקין"}}}},
    "/tiles/hikes/{z}/{x}/{y}.pbf": {"get": {"summary": "אריח מסלולי טיול מתוארים (שכבה hikes)", "parameters": [p("z", ""), p("x", ""), p("y", "")],
                                             "responses": {"200": {"description": "MVT"}, "204": {"description": "ריק"}}}},
})

# online data updates from the weekly GitHub release (hmaps/api/updates.py)
UpdComponent = {"type": "object", "properties": {
    "id": {"type": "string", "enum": ["maps", "basemap", "terrain", "transit", "media", "zmanim"]},
    "name_he": {"type": "string", "examples": ["מפת בסיס"]}, "description_he": {"type": "string"},
    "installed": {"type": ["object", "null"], "description": "version (YYYYMMDD), date, release, sha256, source: online | local | file"},
    "available": {"type": ["object", "null"], "description": "מתוך manifest.json: version, date, file, size, sha256, "
                                                            "status (full | unchanged | unavailable), min_app_version, release"},
    "update_available": {"type": "boolean"}, "compatible": {"type": "boolean", "description": "גרסת התוכנה ≥ min_app_version"},
    "job": {"type": ["object", "null"], "description": "משימת עדכון אחרונה (ראו /api/updates/status)"}}}
UpdJob = {"type": "object", "properties": {
    "component": {"type": "string"},
    "state": {"type": "string", "enum": ["queued", "downloading", "verifying", "installing", "done", "error"]},
    "downloaded": {"type": "integer"}, "total": {"type": "integer"}, "percent": {"type": ["number", "null"]},
    "version": {"type": ["string", "null"]}, "error": {"type": ["string", "null"], "description": "הודעה בעברית"},
    "code": {"type": ["string", "null"], "examples": ["checksum", "offline", "rate_limited", "busy"]},
    "started": {"type": "string"}, "finished": {"type": ["string", "null"]}}}
spec["paths"].update({
    "/api/updates": {"get": {"summary": "עדכוני נתונים: הגרסה המותקנת והגרסה הזמינה של כל רכיב", "parameters": [
        q("refresh", "1 = לקרוא שוב את manifest.json מ-GitHub (אחרת נשמר בזיכרון 15 דקות)")],
        "responses": ok("מצב העדכונים (גם כשאין חיבור: online=false ו-error עם הודעה בעברית)", {"type": "object", "properties": {
            "app_version": {"type": "string"}, "online": {"type": "boolean"}, "error": {"type": ["object", "null"]},
            "release": {"type": ["string", "null"], "examples": ["data-2026-10-22"]}, "generated": {"type": ["string", "null"]},
            "checked_at": {"type": ["string", "null"]}, "updates_available": {"type": "integer"},
            "total_size": {"type": "integer", "description": "בתים להורדה"}, "busy": {"type": "boolean"},
            "components": {"type": "array", "items": UpdComponent}}})}},
    "/api/updates/status": {"get": {"summary": "התקדמות משימות העדכון שרצות ברקע", "responses": ok("משימות", {
        "type": "object", "properties": {"busy": {"type": "boolean"},
                                         "jobs": {"type": "object", "additionalProperties": UpdJob}}})}},
    "/api/updates/{component}": {"post": {
        "summary": "הורדה, בדיקת SHA-256 והתקנה ברקע של רכיב (או all – כל מה שיש לו עדכון)",
        "description": "מותר רק מהמחשב המקומי (HMAPS_UPDATES=local|any|off) ודורש כותרת X-HMaps-Update: 1.",
        "parameters": [p("component", "maps | basemap | terrain | transit | media | zmanim | all"),
                       q("force", "1 = התקנה מחדש גם אם הגרסה עדכנית"),
                       {"name": "X-HMaps-Update", "in": "header", "required": True, "schema": {"type": "string", "enum": ["1"]}}],
        "responses": {"202": {"description": "המשימות נוספו לתור", "content": {"application/json": {"schema": {
            "type": "object", "properties": {"jobs": {"type": "array", "items": UpdJob}}}}}},
                      "200": {"description": "all: אין מה לעדכן"},
                      "403": {"description": "לא מהמחשב המקומי", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                      "404": {"description": "רכיב לא מוכר", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                      "502": {"description": "all: שרת העדכונים לא זמין", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}}}},
})

if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "hmaps" / "api" / "openapi.json"
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(out)
