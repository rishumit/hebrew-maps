# מפות ישראל

<div align="center">

**מפת ישראל מלאה למחשב Windows – בחינם, בעברית, גם בלי אינטרנט**

[![הורדה](https://img.shields.io/github/v/release/rishumit/hebrew-maps?label=הורדה&color=0969da)](https://github.com/rishumit/hebrew-maps/releases/latest/download/HebrewMaps-Setup.exe)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?logo=windows)](https://github.com/rishumit/hebrew-maps/releases/latest/download/HebrewMaps-Setup.exe)
[![חינם](https://img.shields.io/badge/מחיר-חינם-brightgreen)](https://rishumit.github.io/hebrew-maps/)
[![Wiki](https://img.shields.io/badge/Wiki-מדריכים-orange)](https://github.com/rishumit/hebrew-maps/wiki)

[**האתר**](https://rishumit.github.io/hebrew-maps/) · [**הורדה ל-Windows**](https://github.com/rishumit/hebrew-maps/releases/latest/download/HebrewMaps-Setup.exe) · [**מה חדש**](https://rishumit.github.io/hebrew-maps/changelog.html) · [**Wiki**](https://github.com/rishumit/hebrew-maps/wiki)

</div>

---

<p align="center"><img src="docs/images/desktop.png" alt="צילום מסך: חיפוש מסעדות בתל אביב" width="720"></p>

---

## מה זה?

מפות ישראל היא תוכנת מחשב שמורידה את כל מפת ישראל אליכם – ומכאן הכול עובד **מהמחשב שלכם**, גם בלי אינטרנט.

| ✅ בלי אינטרנט | ✅ עברית מלאה | ✅ בלי מעקב | ✅ עדכון שבועי |
|---|---|---|---|
| מפה, חיפוש, ניווט ותחבורה ציבורית שמורים אצלכם | מימין לשמאל בכל מקום | אין חשבון, אין פרסומות, אין שרת שיודע מה חיפשתם | מפה, תחבורה וזמני תפילות, בלחיצה או אוטומטי |

---

## יכולות עיקריות

<details>
<summary><b>🔍 חיפוש שמבין עברית</b></summary>

- שמות: "מגדל דוד", "הכותל"
- כתובות: "דיזנגוף 50 תל אביב", "ירושלים, יפו 97"
- קטגוריות: "מסעדות בתל אביב", "מסעדה כשרה ליד", "בית מרקחת פתוח"
- קיצורים: ת"א, ב"ש, פ"ת, ביה"ח, שד'
- שגיאות כתיב: "דיזינגוף", "מגדל דויד"
- הקלדת אנגלית בטעות: `sh,bdu;` ← דיזנגוף
- קואורדינטות: `32.0853, 34.7818`

</details>

<details>
<summary><b>🚌 תחבורה ציבורית – גם בלי אינטרנט</b></summary>

אוטובוסים, רכבת ורכבת קלה מלוח הזמנים הרשמי של משרד התחבורה, שמור במחשב:
- תכנון נסיעה עם החלפות (עד 4), יציאה עכשיו או בשעה שתבחרו
- חלופות: הגעה מוקדמת / פחות החלפות / פחות הליכה
- לוח יציאות מלא לכל תחנה

</details>

<details>
<summary><b>🧭 ניווט ברכב, ברגל ובאופניים</b></summary>

- מסלולים חלופיים, זמן ומרחק
- הוראות פנייה-פנייה בעברית: "בכיכר צאו ביציאה השנייה לרחוב אלנבי"
- פרופיל גובה עם עלייה וירידה מצטברות בהליכה ואופניים

</details>

<details>
<summary><b>🥾 שבילים וטיולים</b></summary>

- אלפי שבילים מסומנים בצבעיהם (שביל ישראל: 1,080 ק"מ רציף)
- מדריך מסלול לכל שביל: שלבים, זמן הליכה לפי שיפוע, פרופיל גובה, מים, בטיחות
- הדפסה ל-PDF
- טופוגרפיה: קווי גובה, הצללת תבליט ותצוגה תלת-ממדית

</details>

<details>
<summary><b>🕍 זמני תפילות</b></summary>

לוח מניינים שבועי לכל בית כנסת – שחרית, מנחה וערבית – מפרויקט זמני התפילות הארצי של נדרים פלוס. "המניין הבא בסביבה" במרחק לחיצה.

</details>

<details>
<summary><b>📸 תצלומי אוויר – גם אופליין</b></summary>

תצלומי האוויר של מרכז מיפוי ישראל (govmap), לשימוש אישי. כל אזור שנצפה נשמר למחשב. אפשר להוריד מראש את כל הארץ (80MB–4GB לפי רמת פירוט).

</details>

---

## התקנה

### ⬇️ דרך המתקין – הכי מהיר

1. [**הורידו את HebrewMaps-Setup.exe**](https://github.com/rishumit/hebrew-maps/releases/latest/download/HebrewMaps-Setup.exe)
2. הפעילו והמתינו לסיום
3. פתחו את האפליקציה ← **תפריט ← עדכונים ← עדכן הכול** (הורדת נתוני ישראל, כ-2GB, פעם אחת)

> **SmartScreen?** לחצו "מידע נוסף" ← "הפעל בכל זאת". הקובץ נבנה מקוד פתוח ב-GitHub Actions.

---

### 💻 מקוד המקור – בלי Docker

```bat
git clone https://github.com/rishumit/hebrew-maps.git
cd hebrew-maps
setup-windows.cmd
```

לא צריך להתקין שום דבר מראש. Python, Java ו-uv מורדים אוטומטית לתיקייה פרטית, בלי התקנה במערכת.

| שלב | מה נעשה | זמן |
|-----|---------|-----|
| 1 | הורדת Python (uv) | 1–3 דקות |
| 2 | הורדת ספריות ומפת בסיס | דקה |
| 3 | הפעלה + קיצור דרך בשולחן העבודה | מיידי |
| 4 | הורדת OSM ישראל (כ-150MB) | 1–3 דקות |
| 5 | בניית מקומות, רחובות, חיפוש וניווט | 15–30 דקות |
| 6 | בניית מפת בסיס מלאה (Java נייד) | 15–40 דקות |

<details>
<summary>קבצי ניהול</summary>

| קובץ | תפקיד |
|------|-------|
| `start-windows.cmd` | הפעלה |
| `stop-windows.cmd` | עצירה |
| `update-online-windows.cmd` | עדכון נתונים מקוון (מומלץ) |
| `update-online-windows.cmd /schedule` | עדכון אוטומטי שבועי |
| `update-windows.cmd` | בנייה מקומית מחדש (למתקדמים) |
| `build-terrain.cmd` | בניית קווי גובה והצללה |
| `download-satellite.cmd` | הורדת תצלומי אוויר לכל הארץ |

</details>

<details>
<summary>התקנה עם Docker (חלופה)</summary>

```bat
:: 1. התקינו Docker Desktop והקצו לו 6GB זיכרון
:: 2. הריצו:
setup.cmd
:: לאחר מכן: start.cmd / stop.cmd / update-data.cmd
```

ב-Linux/macOS: `./setup.sh`

בגרסת Docker הניווט רץ על OSRM.

</details>

---

## עדכונים

הנתונים נבנים מחדש **כל יום חמישי בלילה** ב-GitHub Actions ומתפרסמים ב-Releases.

| שיטה | איך |
|------|-----|
| מתוך האפליקציה | תפריט ← עדכונים ← "עדכן הכול" |
| שורת פקודה | `update-online-windows.cmd` |
| אוטומטי שבועי | `update-online-windows.cmd /schedule` |
| מחשב בלי אינטרנט | [HebrewMaps-Update.zip](https://github.com/rishumit/hebrew-maps/releases/tag/data-latest) ← תפריט ← "עדכון מקובץ…" |

**עדכון חלקי:** כשהשתנה מעט, יורד רק השינוי (0.1–1.5MB במקום 60MB). המפה ממשיכה לעבוד בזמן העדכון.

<details>
<summary>חבילות הנתונים</summary>

| חבילה | תוכן | תדירות |
|-------|------|---------|
| `maps` | מקומות, רחובות, חיפוש, שבילים, ניווט | שבועית |
| `basemap` | אריחי המפה המלאה (Planetiler) | שבועית |
| `terrain` | קווי גובה, הצללה, פרופיל גובה (Copernicus DEM) | חודשית |
| `transit` | לוחות זמנים (משרד התחבורה GTFS) | שבועית |
| `media` | תמונות אתרים | שבועית |
| `zmanim` | זמני תפילות (נדרים פלוס) | שבועית |

</details>

**רשת מסוננת (נטפרי/רימון):** פתחו `github.com`, `release-assets.githubusercontent.com`, `objects.githubusercontent.com`. שלמות כל קובץ מובטחת ב-SHA-256.

---

## מבנה המערכת

```
דפדפן (HTML/CSS/JS, MapLibre GL)
      │  http://localhost:8080
      ▼
┌──────────────── api (Python/Starlette) ─────────────────┐
│ חיפוש עברי · נקודות עניין · גיאוקודינג · אריחי POI      │
│ מפת בסיס (MBTiles) · גופנים · דוחות · מדדים            │
└───────────┬─────────────────────────────┬───────────────┘
            │ SQLite: R*Tree + FTS5        │ HTTP / בתוך התהליך
            ▼                             ▼
        maps.db              ניווט: OSRM ×3 (Docker) או מנוע פנימי
   (נבנה ע"י etl)               רכב · הליכה · אופניים
            ▲
   etl: OpenStreetMap + data.gov.il + Wikidata/ויקיפדיה
   tiles: Planetiler ← OpenStreetMap  →  israel.mbtiles
```

<details>
<summary>מבנה תיקיות</summary>

```
hmaps/core   עברית: נרמול, קיצורים, תיקון שגיאות, קטגוריות, שעות פתיחה
hmaps/etl    צינור נתונים: osmium → בנייה → העשרה → אינדקס → דוח איכות
hmaps/api    API, מנוע חיפוש, אריחים וקטוריים, ניווט והוראות בעברית
hmaps/route  מנוע ניווט פנימי (גרף דרכים + A*) – בלי Docker
web/         ממשק המשתמש (ללא build): js/, css/, sw.js, manifest
src-tauri/   אפליקציית שולחן העבודה (Tauri 2, Rust)
installer/   PyInstaller + Inno Setup
tests/       יחידה, אינטגרציה, דפדפן (Playwright), ביצועים
docs/        ADR, מימוש, תפעול, API
site/        אתר המוצר (HTML/CSS סטטיים)
```

</details>

ההחלטות הטכניות: [docs/adr](docs/adr) · מסמך מימוש: [docs/11-implementation.md](docs/11-implementation.md) · תפעול: [docs/12-operations.md](docs/12-operations.md)

---

## פיתוח

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium

# בדיקות
python -m unittest tests.test_core tests.test_etl tests.test_api tests.test_router tests.test_native tests.test_terrain tests.test_icons
python -m unittest tests.e2e.test_ui       # דפדפן, נגישות, סגנון מפה
python -m tests.perf_bench 120000          # ביצועים – 120,000 POI
```

הרצה מקומית על נתוני הדגמה:
```bash
python tools/sample_data.py data/sample-src
python -m hmaps.etl from-geojson --poi data/sample-src/poi.geojsonseq --streets data/sample-src/streets.geojsonseq --out data/maps.db
uvicorn hmaps.api.app:app --reload --port 8080
```

<details>
<summary>אפליקציית שולחן העבודה (Tauri/Rust)</summary>

```bash
cd src-tauri && cargo build
# Linux: apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev
HMAPS_URL=http://127.0.0.1:8080/desktop.html ./target/debug/hebrew-maps-desktop
```

</details>

<details>
<summary>פרסום גרסה</summary>

```bash
# 1. כתבו הערות גרסה ב-docs/release-notes/v1.2.0.md
python tools/build_changelog.py
git add docs/release-notes site/changelog.html && git commit -m "release notes v1.2.0" && git push
git tag -a v1.2.0 -m "גרסה 1.2.0" && git push origin v1.2.0
```

או: Actions ← **Windows Installer** ← Run workflow.

</details>

---

## רישוי ומקורות

- **OpenStreetMap** © תורמי OpenStreetMap, [ODbL](https://opendatacommons.org/licenses/odbl/)
- **משרד התחבורה (GTFS)** – לוחות זמנים
- **Copernicus DEM** © DLR e.V. / Airbus Defence and Space GmbH, ESA
- **מרכז מיפוי ישראל (govmap)** – תצלומי אוויר, לשימוש אישי בלבד
- **Sentinel-2 cloudless** © EOX IT Services GmbH, CC BY-NC-SA 4.0
- **נדרים פלוס** – זמני תפילות
- **data.gov.il** – רישיון ממשלתי פתוח · **Wikidata** – CC0 · **ויקיפדיה** – CC BY-SA
- MapLibre GL JS: BSD-3 · OSRM: BSD-2 · Planetiler: Apache-2.0
- קוד הפרויקט: רישיון לקבוע
