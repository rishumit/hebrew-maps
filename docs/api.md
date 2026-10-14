# API – מפות בעברית

מפרט מלא בפורמט OpenAPI 3.1: `GET /api/openapi.json`. כל התשובות ב-JSON (UTF-8). הודעות השגיאה בעברית:

```json
{ "error": { "code": "not_found", "message": "המקום לא נמצא" } }
```

| קוד | מתי |
|-----|-----|
| 400 | פרמטר חסר או לא תקין |
| 404 | לא נמצא |
| 429 | יותר מדי בקשות (מגיע עם `Retry-After`) |
| 503 | הנתונים או הניווט עדיין בהכנה |

## חיפוש

### `GET /api/search/autocomplete?q=&lat=&lon=&limit=8`
הצעות בזמן הקלדה. `lat`/`lon` (מרכז המפה) משפיעים על הדירוג.
```json
{ "suggestions": [ { "id": "place-2", "type": "place", "name": "ירושלים", "secondary": "עיר",
    "location": {"lat": 31.7683, "lon": 35.2137}, "distance": 53634, "icon": "pin", "color": "#4B5563" } ],
  "query_time_ms": 4.1 }
```
`type` הוא אחד מאלה: `place` (יישוב או שכונה), `street`, `poi`, `address`, `category`, `coordinate`, `trail` (שביל מסומן עם שם).

### `GET /api/search?q=…` או `POST /api/search`
```json
POST /api/search
{ "query": "מסעדות בתל אביב", "location": {"lat": 32.08, "lon": 34.78},
  "user_location": {"lat": 32.07, "lon": 34.77}, "filters": {"kosher": true, "open_now": false}, "limit": 20 }
```
התשובה כוללת `results`, ‏`meta` (‏total, ‏returned, ‏query_time_ms) ו-`interpretation`, שמתאר איך השאילתה הובנה:
```json
"interpretation": { "type": "category", "sub": "restaurant", "near": "תל אביב-יפו", "corrected": "…", "converted": "…" }
```

## נקודות עניין
- `GET /api/poi/{id}`: כרטיס מלא (המזהה בפורמט `poi-123`). כולל `address`, ‏`contact`, ‏`opening_hours` (‏`weekly` בדקות מתחילת היום, `rows` לתצוגה, `open_now` ו-`status` כמו "פתוח עכשיו · נסגר ב-17:00"), ‏`media`, ‏`metadata` ו-`description_he`.
- `GET /api/poi?lat=&lon=&radius=1000&category=&tags=&kosher=&open_now=&limit=&offset=`: מקומות בסביבה, ממוינים לפי מרחק וחשיבות.
- `GET /api/categories`: עץ הקטגוריות (מזהה, שם בעברית ובאנגלית, צבע ואייקון).
- `GET /api/place/{id}`, ‏`GET /api/street/{id}`: פרטי יישוב (כולל גבול) ופרטי רחוב (כולל גאומטריה ומספרי בתים).

לנחלים, רכסים, עמקים, שמורות טבע ואתרי רט"ג הכרטיס כולל גם `geometry` (קו או פוליגון ב-GeoJSON, לסימון במפה), `length_m` לנחלים ולרכסים, ו-`nature_site`: ‏`fee` / ‏`fee_he` ("כניסה בתשלום"), ‏`inpa` ו-`inpa_source` (‏`operator` לפי תגית המפעיל, או `name` כשהאתר מזוהה לפי שם מוכרז כמו "גן לאומי …").
חיפוש הקטגוריה "שמורות טבע וגנים לאומיים" כולל גם את אתרי רט"ג. "אתרי רט"ג" מחזיר רק אותם.

## שבילים מסומנים
`GET /api/trails/{id}` (המזהה בפורמט `trail-12`): שביל מ-OSM (relation מסוג `route=hiking|foot|bicycle|mtb`).
```json
{ "id": "trail-1", "type": "trail", "name": "שביל ישראל", "route": "hiking", "route_he": "שביל הליכה",
  "network": "nwn", "network_he": "שביל ארצי", "mark": "orange", "mark_he": "כתום", "color": "#E46F0A",
  "osmc_symbol": "orange:orange:white_right:blue_stripe", "length_km": 1050.3, "parts": 1, "continuous": true,
  "bbox": [34.9, 29.5, 35.6, 33.2], "geometry": {"type": "MultiLineString", "coordinates": [...]},
  "metadata": {"osm_url": "https://www.openstreetmap.org/relation/282071", "source": "OpenStreetMap"} }
```
`mark` הוא צבע הסימון הישראלי (`red`, ‏`blue`, ‏`green`, ‏`black`, ‏`orange`, ‏`purple`, ‏`yellow` או `other`), שנגזר מ-`osmc:symbol`, ואם אין, מ-`colour` או מסוג הרשת. `parts` הוא מספר הקטעים הנפרדים (‏1 = רציף). לשבילים בלי שם יש שם תצוגה כמו "שביל מסומן כחול", והם לא מופיעים בחיפוש.

## זמני תפילות (בתי כנסת)
הנתונים מגיעים מ"פרוייקט זמני תפילות הארצי" של נדרים פלוס (https://www.matara.pro/nedarimplus/zmanim/), ונטענים למסד בפקודה `python -m hmaps.etl zmanim` (ראו `hmaps/etl/zmanim.py`). כל בית כנסת במאגר מקושר לבית הכנסת המתאים ב-OSM לפי מרחק ושם. בתי כנסת שלא נמצאו ב-OSM נוספים כנקודות עניין חדשות (`metadata.source` = "נדרים פלוס"). אם הנתונים לא נטענו, הרשימות ריקות.

- בכרטיס של בית כנסת מקושר, `GET /api/poi/{id}` מחזיר גם `minyanim`:
```json
"minyanim": { "source": "נדרים פלוס – פרוייקט זמני תפילות הארצי", "source_url": "https://www.matara.pro/nedarimplus/zmanim/",
  "mosad_id": "5772843", "name": "תורה ותפילה נוה יעקב", "address": "נוה יעקב",
  "phone": "03-7630585 שלוחה 123", "updated": "2026-10-14",
  "items": [ { "prayer": "שחרית", "time": "06:15", "days": "אבגדה", "hall": "אוהל מרדכי", "note": null } ],
  "sun": { "sunrise": "06:41", "sunset": "18:08" }, "sun_note": "…בקירוב…" }
```
‏`days` הן אותיות הימים (א = ראשון … ו = שישי, ש = שבת). ‏`hall` הוא האולם או החדר בבניין. ‏`sun` מחושב בקירוב, בגובה פני הים, ואינו מחליף לוח זמנים הלכתי.

### `GET /api/minyanim/near?lat=&lon=&radius=1000&prayer=&after=HH:MM&day=&limit=30`
המניינים הבאים בסביבה ב-24 השעות הקרובות: מה שנשאר ביום `day` אחרי `after`, ואחריו היום הבא עד אותה שעה (`tomorrow: true`). המיון הוא לפי שעה ואחר כך לפי מרחק. ברירות המחדל הן עכשיו והיום לפי שעון ישראל. ‏`prayer` יכול להיות שחרית, מנחה, ערבית או שיעור. בלי `prayer` לא מוחזרים שיעורים. ‏`day` מקבל אות (א..ש) או מספר (0 = ראשון). כל תוצאה כוללת `name`, ‏`address`, ‏`poi_id`, ‏`lat`/`lon`, ‏`distance_m` ופרטי המניין.
## מדריך מסלולי טיול
מדריך מובנה (סיכום, שלבים, אתרים, מידע מעשי ובטיחות, קרדיטים) לכל קו הליכה – מסלול מתואר מטבלת `hikes`, שביל מסומן מ-OSM, או מסלול הליכה שתוכנן באפליקציה. המימוש: `hmaps/api/guide.py`.
- `GET /api/hikes?lat=&lon=&radius=20000&limit=20[&geometry=1]` או `?bbox=minlon,minlat,maxlon,maxlat`: מסלולים מתוארים באזור (ממוינים לפי מרחק ממרכז החיפוש). מאגר בלי טבלת `hikes` מחזיר רשימה ריקה.
- `GET /api/hikes/{id}` (‏`hike-12`): כרטיס מסלול – תיאור, דרגת קושי, משך, אורך, מעגלי/קווי, עונה, `waypoints`, ‏`tips`, ‏`water`, ‏`parking`, ‏`geometry`, תמונות וקרדיט המקור.
- `GET /api/hikes/{id}/guide[?date=YYYY-MM-DD]`, ‏`GET /api/trails/{id}/guide[?date=]`: המדריך.
- `POST /api/guide` עם `{"coordinates": [[lon, lat], …], "name": "…", "waypoints": [{"name","lat","lon","note"}], "date": "YYYY-MM-DD"}`: מדריך לקו כלשהו (עד 20,000 נקודות). ‏422 `route_too_long` מעל 300 ק"מ.
```json
{ "kind": "hike", "ref": "hike-1", "name": "…", "paragraphs": ["…"],
  "summary": { "length_m": 6458, "length_text": "6.5 ק״מ", "duration_min": 120, "duration_text": "שעתיים",
    "time_formula": "tobler|naismith|flat", "duration_source_min": 180, "ascent": 420, "descent": 120,
    "min_ele": -350, "max_ele": 80, "max_grade_pct": 22, "difficulty": "moderate", "difficulty_he": "בינוני",
    "difficulty_source": "source|estimated", "loop": false, "marks": [{"id": "trail-1", "name": "שביל ישראל", "mark": "orange", "color": "#E46F0A", "share": 0.79}],
    "stream": false, "season": "…" },
  "start": {"name": "חניון …", "lat": 31.41, "lon": 35.36}, "end": {…},
  "stages": [ { "n": 1, "from": {"name": "…", "at_m": 0}, "to": {"name": "…", "at_m": 2418, "kind": "waypoint|junction|sight|distance"},
    "distance_m": 2418, "duration_min": 36, "ascent": 120, "descent": 10, "marks": [...], "stream": "נחל דוד",
    "sights": [ {"id": "poi-39", "name": "עין דוד", "sub": "spring", "at_m": 2300, "distance": 40, "text": {"text": "…", "source_he": "ויקיפדיה"}} ],
    "instruction": "יוצאים מחניון … בשביל ישראל בעלייה כ-2.4 ק״מ עד מצפה …", "bbox": [...], "geometry": {"type": "LineString", …} } ],
  "sights": [...], "elevation": {"profile": [[d, ele, lon, lat], …], "ascent": …} | null,
  "practical": { "water": {"points": [...], "springs": [...], "liters": 2, "note": "…"}, "toilets": [...], "parking": [...],
    "shelters": [...], "shade": "…", "daylight": {"sunrise": "06:40", "sunset": "18:08", "latest_start": "15:40", "note": "…"},
    "emergency": {"phones": [{"number": "112", …}], "nearby": [...]}, "tips": [{"kind": "flood|heat|long|shuttle|steep|navigation|general|source", "text": "…"}] },
  "credits": [{"role": "תיאור המסלול", "name": "עמוד ענן", "url": "…", "license": "…"}, …], "geometry": {…}, "bbox": [...] }
```
**זמן הליכה**: פונקציית ההליכה של טובלר, `v = 6·exp(-3.5·|שיפוע + 0.05|)` קמ"ש, לכל צעד של ~25–40 מ' בפרופיל הגובה, מוכפלת ב-0.8 כך שבשטח מישורי הקצב 4 קמ"ש. בלי נתוני גובה: 4 קמ"ש (ועוד שעה לכל 600 מ' עלייה – כלל נייסמית – כשהמקור מציין עלייה). המשך לפי המקור מוחזר בנפרד (`duration_source_min`).
**חלוקה לשלבים**: לפי נקודות הציון של המקור; צמתים שבהם משתנה השביל המסומן שהולכים בו; אתרים בולטים (מעיין, תצפית, עתיקות, פסגה, מערה, מפל) כשהם רחוקים מספיק מחיתוכים אחרים; ושלב ארוך מ-4 ק"מ מתפצל באתר / ברזייה / סככה הקרובים לאמצעו, או שווה בשווה כל ~3 ק"מ. **דרגת קושי** כשאין במקור: ק"מ + עלייה/100 (<8 קל, <15 בינוני, <24 קשה), ודרגה אחת יותר כשיש שיפוע של מעל 25%. **שעות אור** מחושבות אסטרונומית (משוואת הזריחה) לנקודת המוצא, בשעון ישראל.

## גאוקודינג
- `GET /api/geocode?address=דיזנגוף 50&city=תל אביב`: מחזיר `lat`, ‏`lon`, ‏`address`, ‏`confidence` ו-`accuracy`. ערכי `accuracy`: ‏`exact`, ‏`interpolated` (מספר קרוב), ‏`street` (מרכז הרחוב).
- `GET /api/reverse?lat=&lon=`: מחזיר `street`, ‏`house_number`, ‏`city`, ‏`neighbourhood`, ‏`address` ו-`accuracy`.

## ניווט
`GET /api/route?from=lat,lon&to=lat,lon&mode=car|foot|bike[&via=lat,lon][&alternatives=1]`
```json
{ "mode": "car", "routes": [ { "distance": 1399.7, "duration": 145, "distance_text": "1.4 ק״מ", "duration_text": "2 דק׳",
  "summary": "דרך שדרות רוטשילד ודיזנגוף", "geometry": {"type": "LineString", "coordinates": [...]},
  "steps": [ { "instruction": "פנו שמאלה לשדרות רוטשילד", "distance_text": "840 מ׳", "maneuver": {...} } ] } ] }
```

## תחבורה ציבורית
הזמנים נלקחים מלוח הזמנים הרשמי של משרד התחבורה (GTFS), ולכן הם **זמנים משוערים לפי לוח הזמנים**, לא זמני אמת. אם הנתונים לא הותקנו (`python -m hmaps.etl gtfs`), כל נקודות הקצה מחזירות ‎503 עם הקוד `transit_not_ready` והודעה בעברית. החריגה היחידה היא `status`, שמחזירה ‎200 עם `available: false`.

### `GET /api/transit?from=lat,lon&to=lat,lon&time=ISO&mode=depart|arrive&limit=5`
- `time`: זמן בפורמט ISO 8601. אם לא מצוין אזור זמן, הזמן נקרא כשעון ישראל. ברירת המחדל היא עכשיו.
- `mode`: ‏`depart` (יציאה בזמן הנתון או אחריו) או `arrive` (הגעה עד הזמן הנתון).
```json
{ "mode": "depart", "estimated": true, "estimated_text": "זמנים משוערים לפי לוח הזמנים",
  "reason": null, "notice": null, "next_departure": null,
  "itineraries": [ { "departure": "2026-10-14T08:04:00+03:00", "arrival": "2026-10-14T08:53:00+03:00",
      "departure_text": "08:04", "arrival_text": "08:53", "duration": 2940, "duration_text": "49 דק׳",
      "transfers": 0, "walk_distance": 171, "summary": "רכבת", "tags": ["earliest_arrival", "fastest"],
      "legs": [
        { "type": "walk", "kind": "access", "instruction": "הליכה דקה (30 מ׳) לתחנה תל אביב מרכז", "geometry": {…} },
        { "type": "rail", "mode_he": "רכבת", "line": "רכבת", "agency": "רכבת ישראל", "headsign": "ירושלים/יצחק נבון",
          "train_number": "723", "color": "#…", "text_color": "#…", "from": {"id": "…", "name": "תל אביב מרכז", "code": 17038, "platform": "3"},
          "to": {…}, "departure_text": "08:05", "arrival_text": "08:52", "num_stops": 4, "stops": [{"name": "…", "time_text": "08:05"}],
          "instruction": "רכבת לכיוון ירושלים/יצחק נבון מתחנת תל אביב מרכז, ירידה בתחנת ירושלים/יצחק נבון אחרי 4 תחנות",
          "geometry": {"type": "LineString", "coordinates": […]} } ] } ],
  "feed": { "feed_start": "2026-10-13", "feed_end": "2026-11-12", "freshness": "ok", "warning": null } }
```
- **סוגי קטעים (`type`):** ‏`walk`, ‏`bus`, ‏`rail`, ‏`light_rail`, ‏`subway`, ‏`funicular` (הכרמלית), ‏`cable_car`, ‏`share_taxi`, ‏`ferry`. ל-`walk` יש גם `kind`, אחד מ-`access`, ‏`transfer`, ‏`egress`.
- **תגיות (`tags`):** ‏`earliest_arrival`, ‏`latest_departure` (במצב "הגעה עד"), ‏`fastest`, ‏`fewest_transfers`, ‏`least_walking`, ‏`walk_only`.
- **`reason` ו-`notice`:** מסבירים מצב חריג.

| `reason` | מתי |
|----------|-----|
| `shabbat` | שבת, או יום שישי אחר הצהריים |
| `shabbat_limited` | בשבת נמצאו רק קווים שפועלים בשבת |
| `holiday` | חג או יום שבו השירות מצומצם מאוד |
| `night` | שעות הלילה |
| `no_route` | לא נמצא מסלול |
| `out_of_range` | התאריך מחוץ ללוח הזמנים |
| `no_stops_nearby` | אין תחנה במרחק הליכה |

  כשאין שירות בזמן המבוקש, `next_departure` מציין את היציאה הבאה, ו-`itineraries` כבר מכיל נסיעות שיוצאות ממנה.

### `GET /api/transit/stop/{id}?time=ISO&limit=20`
היציאות הקרובות מתחנה (בשלוש השעות הבאות). `id` הוא ה-`stop_id` שמופיע ב-`from`/`to` של קטע נסיעה, או `code-<מספר התחנה>` (המספר שעל השלט). התשובה כוללת `stop`, את `lines` (הקווים שעוצרים בתחנה, עם צבעים) ואת `departures`. בכל יציאה יש `time_text`, ‏`minutes`, ‏`line`, ‏`headsign`, ‏`agency` ו-`color`.

### `GET /api/transit/stops?lat=&lon=&radius=500`
תחנות בסביבה, ממוינות לפי מרחק.

### `GET /api/transit/status`
תאריכי התחלה וסיום של לוח הזמנים (`feed_start`, ‏`feed_end`), זמן הבנייה (`built`), ומדד העדכניות `freshness`. ערכי `freshness`: ‏`ok`, ‏`stale` (עברו יותר מ-14 יום מהעדכון), ‏`expiring` (נשארו עד 5 ימים), ‏`expired`. כשצריך, `warning` מכיל אזהרה בעברית.

## גובה (Copernicus DEM)
זמין אחרי בניית השכבות הטופוגרפיות (`python -m tools.terrain build`). עד אז מוחזר 503 עם `terrain_not_ready`.

- `GET /api/elevation?points=31.7767,35.2345|31.5,35.5`: גובה (במטרים) לכל נקודה (`lat,lon`, מופרדות ב-`|` או ב-`;`, עד 1000):
```json
{ "elevations": [734.8, -427.0], "points": [[31.7767, 35.2345], [31.5, 35.5]], "unit": "m",
  "attribution": "Copernicus DEM © DLR/Airbus, ESA" }
```
- `GET /api/elevation?path=<polyline>&samples=200`: פרופיל גובה לאורך קו (Google polyline, דיוק 5). הקו נדגם במרווחים שווים (2–500 דגימות, ולא צפוף מ-10 מ'):
```json
{ "distance": 3290.3, "ascent": 554, "descent": 34, "min": -327, "max": 204, "start": -327, "end": 204,
  "profile": [[0.0, -327.1, 35.3877, 31.461], [20.7, -325.0, 35.38749, 31.46108], …], "unit": "m", "attribution": "…" }
```
כל שורה ב-`profile` היא `[מרחק מההתחלה במטרים, גובה, lon, lat]`. בעלייה ובירידה המצטברות מתעלמים מתנודות של פחות מ-2 מ'.
- `POST /api/elevation`: אותו דבר בגוף JSON, לקווים ארוכים: `{"coordinates": [[lon, lat], …], "samples": 160}` (סדר GeoJSON, כמו `geometry.coordinates` של מסלול), או `{"points": [[lat, lon], …]}`.

`/api/meta` כולל `terrain`: ‏`{"contours": {tiles, minzoom, maxzoom, bounds} | null, "dem": {…, "encoding": "terrarium"} | null, "elevation": true|false}`.

## אריחים וגופנים
| נתיב | תוכן |
|------|------|
| `/tiles/base.json` | TileJSON של מפת הבסיס (OpenMapTiles) |
| `/tiles/base/{z}/{x}/{y}.pbf` | אריחי מפת הבסיס |
| `/tiles/poi/{z}/{x}/{y}.pbf?cats=&kosher=` | שכבת נקודות העניין (שכבה `pois`: ‏id, ‏name, ‏cat, ‏sub, ‏icon, ‏rank; ובקיבוץ: ‏count, ‏cluster) |
| `/tiles/fallback/{z}/{x}/{y}.pbf` | מפת גיבוי מהמאגר (שכבות `streets`, ‏`places`, ‏`boundaries`) |
| `/tiles/trails/{z}/{x}/{y}.pbf` | שבילים מסומנים (שכבה `trails`: ‏id, ‏name, ‏route, ‏network, ‏mark, ‏bike, ‏rank). בזום 7–9 רק שבילים ארציים ואזוריים, מזום 12 כולם |
| `/tiles/hikes/{z}/{x}/{y}.pbf` | מסלולי טיול מתוארים מזום 8 (שכבה `hikes`: ‏id, ‏name, ‏difficulty, ‏length_m, ‏circular). ‏204 כשאין |
| `/tiles/parks/{z}/{x}/{y}.pbf` | שמורות טבע, גנים לאומיים ואתרי רט"ג מזום 8 (שכבה `parks`, פוליגונים: ‏id, ‏name, ‏sub, ‏inpa, ‏inpa_source) |

| `/tiles/contours/{z}/{x}/{y}.pbf` | קווי גובה, z10–14 (שכבה `contour`: ‏`ele`, ‏`idx`=1 בקו ראשי; שכבה `spot`: ‏`ele`). ‏204 כשאין |
| `/tiles/terrain/{z}/{x}/{y}.png` | גובה בקידוד Terrarium ‏(raster-dem), ‏z5–12. מעל הים: אריח גובה 0. ‏404 כשהשכבה לא נבנתה |
| `/fonts/{fontstack}/{range}.pbf` | גליפים לתוויות |

## מערכת
`/api/health`, ‏`/api/meta`, ‏`/metrics` (Prometheus), ‏`/reports` (דוח איכות נתונים).
