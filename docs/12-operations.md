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
`update-data.cmd` מוריד גרסה חדשה של OSM, בונה מאגר חדש ומחליף אותו **בלי השבתה** (החלפה אטומית ו-hot reload). אחר כך הוא בונה מפת בסיס חדשה ומפעיל מחדש את הניווט. הניווט לא זמין לכ-15 דקות, עד שהגרפים החדשים מוכנים.
תדירות מומלצת: חודשית (בהתאם למסמך 03 §7).

### תחבורה ציבורית: עדכון שבועי
לוח הזמנים של משרד התחבורה (GTFS) מתפרסם מחדש כל יום, ותקף לכ-30 יום קדימה. לכן **מומלץ לעדכן אותו פעם בשבוע**. העדכון לוקח 2–5 דקות ודורש כ-1GB זיכרון פנוי, והמפה ממשיכה לעבוד בזמן העדכון. השרת עובר לנתונים החדשים לבד, דרך הקובץ `data/transit/transit.current`.

| איפה | פקודה |
|------|-------|
| Windows בלי Docker | לחיצה כפולה על `update-transit-windows.cmd`. העדכון גם נכלל ב-`update-windows.cmd` |
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
