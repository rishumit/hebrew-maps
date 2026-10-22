# מדריך תפעול – מפות בעברית

## שירותים

| שירות | קונטיינר | פורט | הערות |
|-------|----------|------|-------|
| אתר + API | `hebrew-maps-api-1` | 8080 (ניתן לשינוי ב-`.env`) | ‏2 workers, ‏healthcheck על `/api/health` |
| ניווט | `hebrew-maps-routing-1` | פנימי 5001–5003 | מכין גרפים בהפעלה ראשונה / כשהנתונים מתעדכנים |
| בניית נתונים | `etl` (משימה) | – | `docker compose run --rm etl` |
| מפת בסיס | `tiles` (משימה) | – | `docker compose run --rm tiles` |

כל הנתונים נשמרים ב-volume בשם `hebrew-maps_hmaps-data`:
`maps.db` (המאגר), `tiles/israel.mbtiles` (מפת הבסיס), `routing/` (גרפי ניווט), `sources/` (קבצי מקור), `reports/` (דוחות איכות).

## פקודות נפוצות (בתיקיית הפרויקט)

```bat
start.cmd                          :: הפעלה
stop.cmd                           :: עצירה
update-data.cmd                    :: עדכון כל הנתונים (OSM חדש, מאגר, מפה, ניווט)
docker compose ps                  :: מצב השירותים
docker compose logs -f api         :: לוג האתר (JSON, ללא מילות החיפוש של המשתמשים)
docker compose logs -f routing     :: התקדמות הכנת הניווט
docker compose run --rm etl python -m hmaps.etl build --no-enrich   :: בנייה ללא ויקיפדיה ו-data.gov.il
docker compose run --rm etl python -m hmaps.etl report              :: יצירה מחדש של דוח האיכות
```

## ניטור
- **תקינות:** `http://localhost:8080/api/health`. החזרת 200 פירושה שיש נתונים. בנוסף, `docker compose ps` מציג healthy/unhealthy.
- **מצב השירותים והנתונים:** `http://localhost:8080/api/meta`: תאריך הנתונים, מפת בסיס מלאה או פשוטה, וזמינות ניווט לכל אמצעי.
- **מדדים (Prometheus):** `http://localhost:8080/metrics`: מספר בקשות לפי נתיב וסטטוס, והיסטוגרמת זמני תגובה.
- **איכות נתונים:** `http://localhost:8080/reports`.

כללי התראה מומלצים (מותאמים מ-06-deployment-ops §4.3): שיעור 5xx מעל 1% ל-5 דקות; p95 של `api_search` מעל 300ms; `/api/health` שלא מחזיר 200 במשך 2 דקות.

## גיבוי ושחזור
המאגר נבנה מחדש מנתונים פתוחים, ולכן גיבוי אינו הכרחי. בכל זאת, כדי לחסוך זמן בנייה:
```bat
docker run --rm -v hebrew-maps_hmaps-data:/data -v "%cd%":/backup alpine tar czf /backup/hmaps-backup.tgz -C /data maps.db tiles
docker run --rm -v hebrew-maps_hmaps-data:/data -v "%cd%":/backup alpine tar xzf /backup/hmaps-backup.tgz -C /data
```

## עדכון נתונים

### עדכון מקוון (ברירת המחדל)
הנתונים נבנים פעם בשבוע ב-GitHub Actions ומתפרסמים ב-GitHub Releases. התוכנה מורידה ומתקינה אותם בעצמה:

| איפה | איך |
|------|-----|
| בממשק | תפריט ← **עדכונים** ("עדכן" לחבילה / "עדכן הכול"; בדיקה שבועית אוטומטית עם הודעה קטנה – אפשר לכבות בדף) |
| Windows | `update-online-windows.cmd` (‏`/check` – רק בדיקה, ‏`/quiet` – ליומן `data\reports\update-log.txt`) |
| כל מערכת | `python -m hmaps update [--check] [--component maps] [--force]`, או קובץ ZIP מקומי: `--component transit --zip hmaps-transit-20261022.zip` |
| Docker | `docker compose exec api python -m hmaps update` (בבקשות מהדפדפן ה-API ב-Docker לא מזהה את המחשב כמקומי; ‏`HMAPS_UPDATES=any` מתיר, רק כשהשרת לא חשוף לרשת) |
| API | ‏`GET /api/updates`, ‏`POST /api/updates/{component|all}` (רק מ-localhost, עם הכותרת `X-HMaps-Update: 1`), ‏`GET /api/updates/status` – ראו [api.md](api.md#עדכוני-נתונים) |

עדכון אוטומטי כל יום שישי ב-06:10 (אחרי הפרסום ביום חמישי בלילה):
```bat
update-online-windows.cmd /schedule
:: או ידנית:
schtasks /Create /F /SC WEEKLY /D FRI /ST 06:10 /TN "HebrewMaps online update" /TR "\"%cd%\update-online-windows.cmd\" /quiet"
```

**איך זה עובד** (`hmaps/api/updates.py`):
1. קריאת `manifest.json` מ-`https://github.com/rishumit/hebrew-maps/releases/download/data-latest/manifest.json` (הורדה רגילה – בלי מגבלת הקצב של ה-API). אם הקובץ לא נמצא, נעשה ניסיון דרך GitHub REST API; כשהמגבלה של 60 בקשות בשעה מתמלאת מוצגת הודעה עם השעה שבה אפשר לנסות שוב.
2. השוואה לגרסאות המותקנות ב-`data/components.json`. נתונים שנבנו מקומית (בלי רשומה) מזוהים לפי תאריך הבנייה, וגרסה מקומית חדשה יותר לא מוחלפת בישנה. רכיב עם `min_app_version` גבוה מגרסת התוכנה מסומן "דורש גרסה חדשה".
3. הורדה ל-`data/updates/downloads/` עם המשך הורדה (HTTP Range), ‏4 ניסיונות והמתנה הולכת וגדלה, ובדיקת גודל ו-SHA-256. קובץ שלא עבר את הבדיקה נמחק ולא מותקן.
4. חילוץ ל-`data/updates/staging/<component>/` (רק קבצים שמתאימים למבנה הרכיב, בלי נתיבים חשודים), ואז החלפה אטומית:
   `maps.db` דרך `db.publish` ‏(`maps-<time>.db` + `maps.current`, וזמני התפילות המקומיים מוחלים מחדש על המאגר החדש), אריחים כ-`data/tiles/<name>-<time>.mbtiles` חדש (השרת מגיש את החדש ביותר), `transit.npz` דרך `transit.current`, תמונות מתווספות ל-`data/media/`, וזמני התפילות נשמרים ומוחלים על המאגר החי. לבסוף השרת טוען מחדש מיד (`reload()`), בלי הפעלה מחדש.
5. נעילה (`data/updates/.lock`) מונעת מהשרת ומהמשימה המתוזמנת להתקין בו-זמנית.

ברשת מסוננת (נטפרי וכו') Python לפעמים דוחה את תעודת האבטחה של הסינון. במקרה כזה, כמו ב-`tools/terrain.py`, הבקשה נשלחת שוב בלי אימות תעודה. זה בטוח, כי כל קובץ נבדק מול ה-SHA-256 שבמניפסט. אם הסינון חוסם את ההורדה, צריך לבקש לפתוח את `github.com` ואת `release-assets.githubusercontent.com` / `objects.githubusercontent.com`.

משתני סביבה: `HMAPS_UPDATES_REPO` (ברירת מחדל `rishumit/hebrew-maps`), ‏`HMAPS_UPDATES_MANIFEST` (כתובת או נתיב מקומי, למשל למראה פנימית), ‏`HMAPS_UPDATES=local|any|off` (מי רשאי להתקין דרך ה-API; ברירת המחדל: רק המחשב המקומי).

### עדכון במחשב בלי אינטרנט (קובץ עדכון)
כל ריצה שבועית מפרסמת גם **קובץ עדכון** אחד עם כל הרכיבים: ‏`HebrewMaps-Update-YYYY-MM-DD.zip` בגרסה המתוארכת, ואותו קובץ בשם קבוע `HebrewMaps-Update.zip` ב-`data-latest` (הקישור באתר: `download.html#offline`). מעל כ-2GB (מגבלת GitHub לקובץ) הוא מתחלק ל-`-part1`, ‏`-part2`… וכל חלק עומד בפני עצמו.

- **מבנה:** ZIP לא דחוס עם `manifest.json` (רק הרכיבים שבקובץ, ‏`"bundle": {"part", "parts", "date"}`), ה-ZIP של כל רכיב, ו-`README.txt` בעברית. נבנה ב-`tools/release.py bundle`; רכיבים שלא נבנו מחדש השבוע מורדים מהגרסה הקודמת שלהם.
- **התקנה:** עדכונים ← "עדכון מקובץ…" (הקובץ נשלח לשרת המקומי ב-`POST /api/updates/offline`, נשמר זמנית ב-`data/updates/offline/` ונמחק בסוף), או בשורת הפקודה: `python -m hmaps update --bundle E:\HebrewMaps-Update-2026-10-22.zip`.
- כל רכיב מועתק מתוך הקובץ ונבדק מול הגודל וה-SHA-256 שב-manifest, בדיוק כמו בהורדה מקוונת, ומותקן רק אם הוא חדש מהמותקן. ב-`components.json` הוא נרשם עם `"source": "offline"`.

### פרסום הנתונים: ‏`.github/workflows/weekly-data.yml`
רץ כל יום חמישי ב-20:00 UTC (22:00 או 23:00 בישראל), וגם ידנית (Actions ← Weekly data release ← Run workflow), עם תיבת סימון לכל רכיב.

```
plan ─┬─ osm ─┬─ maps ── media ─┐
      │       └─ basemap ───────┤
      ├─ terrain (חודשי) ───────┤
      ├─ transit ───────────────┼─ publish
      └─ zmanim ────────────────┘
```

| משימה | מה היא עושה | מטמון |
|-------|-------------|-------|
| plan | ‏`tools/release.py plan`: אילו רכיבים לבנות. בתזמון: הכול, ונתוני הגובה רק בשבוע הראשון של החודש (או כשעוד לא פורסמו). בהרצה ידנית: הרכיבים המסומנים, ובנוסף כל רכיב שעוד לא פורסם אף פעם (כך שהריצה הראשונה בונה הכול). בריצה הראשונה (`previous=false`) כל ההורדות של `previous-manifest` מדולגות | – |
| osm | הורדת ה-PBF מ-Geofabrik ‏(wget, ‏5 ניסיונות, בדיקת MD5 כשמתפרסם קובץ ‎.md5), פעם אחת לכל ריצה | PBF לפי שבוע |
| maps | ‏`hmaps.etl build --engine pyosmium --no-enrich` + ‏`enrich` (ויקינתונים, ויקיפדיה, ויקימסע, ויקישיתוף, שבילים) | תשובות HTTP של ההעשרה |
| basemap | ‏`tools/basemap.py` (Java 21, ‏`--osm_path`, ‏`--http_timeout=120s`, זיכרון: קלט `planetiler_memory` או משתנה `HMAPS_PLANETILER_MEMORY`, ברירת מחדל 3g) | Planetiler + מקורות קו חוף |
| terrain | ‏`python -m tools.terrain build` | אריחי ה-DEM (קבוע) |
| transit | ‏`hmaps.etl gtfs --force` | – |
| zmanim | מתחיל מהתמונה הקודמת (שמות בתי כנסת), ‏`hmaps.etl zmanim --fetch` | – |
| media | ‏`tools/media.py` על ה-maps.db של הריצה (או של הגרסה הקודמת אם לא נבנה) | התמונות שכבר הורדו |
| publish | אימות, ‏`manifest.json`, יצירת הגרסה המתוארכת, עדכון `data-latest`, קובץ העדכון ללא אינטרנט, ומחיקת גרסאות ישנות | – |

כל משימת רכיב מסיימת ב-`tools/release.py pack` (ZIP במבנה הקבוע) וב-`tools/release.py verify` (מבנה, ‏`PRAGMA quick_check`, מספר שורות – למשל לפחות 10,000 נקודות עניין). כשרכיב נכשל, ה-publish ממשיך עם השאר, והרכיב שנכשל נשאר בגרסה הקודמת שלו.

**מבנה הגרסאות ב-GitHub Releases:**

| גרסה (tag) | תוכן |
|------------|------|
| `data-YYYY-MM-DD` | ‏`hmaps-<component>-<YYYYMMDD>.zip` לכל רכיב שנבנה בריצה + ‏`manifest.json` + קובץ העדכון ללא אינטרנט `HebrewMaps-Update-YYYY-MM-DD.zip`. לא מסומנת "Latest", כדי שגרסת התוכנה (`v*`, ‏installer.yml) תישאר האחרונה |
| `data-latest` | ‏pre-release שמכיל את `manifest.json` העדכני (מצביע) ואת `HebrewMaps-Update.zip` (קובץ העדכון ללא אינטרנט בשם קבוע) |

‏`manifest.json` מפרט תמיד את כל הרכיבים. רכיב שלא נבנה מחדש מקבל `"status": "unchanged"` ומצביע על הקובץ מגרסה קודמת, ורכיב שעוד לא פורסם מקבל `"unavailable"`:
```json
{"schema": 1, "repo": "rishumit/hebrew-maps", "release": "data-2026-10-22", "date": "2026-10-22", "min_app_version": "1.0.0",
 "components": {"terrain": {"status": "unchanged", "version": "20261001", "date": "2026-10-01", "release": "data-2026-10-01",
   "file": "hmaps-terrain-20261001.zip", "size": 198000000, "sha256": "…", "url": "https://github.com/…/hmaps-terrain-20261001.zip",
   "name_he": "נתוני גובה וטופוגרפיה", "description_he": "…", "min_app_version": "1.0.0"}, "…": {}}}
```
בכל ZIP יש `component.json` (רכיב, גרסה, commit). מבנה הקבצים בכל רכיב מוגדר ב-`hmaps/core/components.py` ומשותף ל-CI ולתוכנה. הגרסאות המתוארכות נשמרות 6 שבועות, וגרסה שה-manifest עדיין מפנה אליה לא נמחקת.

### תצלומי אוויר לכל הארץ: ‏`.github/workflows/satellite.yml`
הרצה ידנית בלבד (Actions ← Aerial photos (offline package) ← Run workflow). מורידה את תצלומי govmap לכל הארץ עד הזום שנבחר (ברירת מחדל 16: כ-158,000 אריחים, כ-4GB, כ-9 שעות) ל-`satellite.mbtiles`.
- כל סבב עוצר מעצמו אחרי `minutes` (ברירת מחדל 320), שומר את האריחים במטמון של Actions ומפעיל את הסבב הבא בעצמו, עד שסבב עובר על כל האריחים (עד `max_rounds`).
- בסיום: `tools/sat_pack.py pack` מחלק את הקובץ לחלקים מתחת ל-2GB ‏(`HebrewMaps-Satellite-z16.mbtiles.001`…) ויוצר `HebrewMaps-Satellite-z16-install.cmd`, שמחבר אותם ב-Windows, בודק SHA-256 ומתקין ל-`data\tiles\satellite.mbtiles`.
- הפלט הוא **טיוטת ריליס** `satellite-z16` – גלויה רק למי שיש לו הרשאת כתיבה. התצלומים מוגבלים לשימוש אישי לפי תנאי govmap, ולכן אין לפרסם את הטיוטה.
- הרצה חוזרת מרעננת: אריחים שכבר במטמון נשמרים. להורדה מלאה מחדש מוחקים את המטמון `satellite-z…` ב-Actions ← Caches.

### בנייה מקומית (למתקדמים)
`update-data.cmd` (Docker) / ‏`update-windows.cmd` מורידים גרסה חדשה של OSM, בונים מאגר חדש ומחליפים אותו **בלי השבתה** (החלפה אטומית ו-hot reload). אחר כך הם בונים מפת בסיס חדשה ומפעילים מחדש את הניווט. הניווט לא זמין לכ-15 דקות, עד שהגרפים החדשים מוכנים.

### תחבורה ציבורית: עדכון שבועי
לוח הזמנים של משרד התחבורה (GTFS) מתפרסם מחדש כל יום, ותקף לכ-30 יום קדימה. לכן **מומלץ לעדכן אותו פעם בשבוע**. העדכון לוקח 2–5 דקות ודורש כ-1GB זיכרון פנוי, והמפה ממשיכה לעבוד בזמן העדכון. השרת עובר לנתונים החדשים לבד, דרך הקובץ `data/transit/transit.current`.

| איפה | פקודה |
|------|-------|
| מקוון (מומלץ) | תפריט ← עדכונים, או `python -m hmaps update --component transit` |
| Windows בלי Docker, בנייה מקומית | לחיצה כפולה על `update-transit-windows.cmd`. העדכון גם נכלל ב-`update-windows.cmd` |
| Docker | `docker compose run --rm etl python -m hmaps.etl gtfs --force` (נכלל ב-`update-data.cmd`) |
| מקובץ שהורד ידנית | `python -m hmaps.etl gtfs --zip israel-public-transportation.zip` |

עדכון אוטומטי ב-Windows, כל יום ראשון ב-06:00 (הפקודה מורצת פעם אחת, משורת הפקודה בתיקיית הפרויקט):
```bat
schtasks /create /tn "Hebrew Maps transit" /sc weekly /d SUN /st 06:00 /tr "\"%cd%\update-transit-windows.cmd\" /quiet" /f
```

**מעקב:** ‏`GET /api/transit/status` מחזיר את תאריכי התחלה וסיום של לוח הזמנים ואת `freshness`. בממשק מוצגת אזהרה כשעברו יותר מ-14 יום מהעדכון (`stale`), כשנשארו 5 ימים או פחות (`expiring`), וכשלוח הזמנים פג (`expired`). לאחר תאריך הסיום אי אפשר לתכנן נסיעות.

## פרסום ברשת (אופציונלי)
גישה ממחשבים אחרים ברשת המקומית: `http://<שם-המחשב>:8080`. שימו לב: זיהוי מיקום בדפדפן עובד רק ב-localhost או ב-HTTPS.
לדומיין ציבורי עם HTTPS אוטומטי:
```bat
set HMAPS_DOMAIN=maps.example.org
docker compose --profile https up -d
```

## אבטחה
- השרת רץ כמשתמש לא מורשה (uid 10001), עם הגבלת קצב לכל לקוח (`HMAPS_RATE_PER_SEC`, ‏`HMAPS_RATE_BURST`).
- כותרות אבטחה: CSP מחמיר (בלי inline scripts), ‏nosniff, ‏Referrer-Policy, ו-Permissions-Policy.
- ולידציה לכל הפרמטרים, הגבלת אורך שאילתות, והגנה מ-path traversal בגופנים ובדוחות.
- הלוגים אינם כוללים מחרוזות חיפוש.

## תקלות נפוצות

| תסמין | פתרון |
|-------|-------|
| "Docker Desktop is not installed / not running" | להתקין ולהפעיל את Docker Desktop, ואז להריץ שוב את `setup.cmd` |
| הבנייה נעצרת עם "Killed" או "out of memory" | להגדיל את הזיכרון של Docker ל-6–8GB (Settings ← Resources), או להקטין `PLANETILER_MEMORY` ב-`.env` |
| "שירות הניווט אינו זמין כרגע" | הניווט עדיין בהכנה: `docker compose logs -f routing` |
| מוצגת "מפה פשוטה" | מפת הבסיס עדיין לא נבנתה: `docker compose run --rm tiles` (ראו setup.ps1, שלב 7) |
| הפורט 8080 תפוס | `HMAPS_PORT=8090` בקובץ `.env`, ואז `start.cmd` |
| שמות בעברית מוצגים הפוך במפה | חסר תוסף ה-RTL. לבנות מחדש את האימג': `docker compose build --no-cache api` |
