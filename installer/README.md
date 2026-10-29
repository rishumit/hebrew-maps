# בניית מתקין Windows — מפות ישראל
# Windows Installer Build Guide — Hebrew Maps

---

## דרישות מקדימות / Prerequisites

| כלי / Tool | גרסה / Version | הורדה / Download |
|---|---|---|
| Windows | 10/11 x64 | — |
| Python 3.12 + `.venv` | כלול בפרויקט / Included | הריצו `setup-windows.cmd` |
| Inno Setup 6 | 6.x | https://jrsoftware.org/isinfo.php |
| Rust (MSVC) — לאפליקציית שולחן העבודה / for the desktop app | stable | https://rustup.rs (+ Visual Studio Build Tools, "Desktop development with C++") |

Rust אופציונלי: בלעדיו המתקין נבנה כמו קודם (פתיחה בדפדפן). / Rust is optional: without it the installer is built as before (opens the browser).

**אין צורך בתוכנה נוספת.** Python מוטמע בתוך ה-EXE שנוצר — המשתמש הסופי אינו צריך להתקין Python.

**No additional software needed for end users.** Python is embedded inside the produced EXE.

---

## בנייה במכה אחת / One-command build

פתחו PowerShell מתיקיית שורש הפרויקט והריצו:

Open PowerShell from the project root and run:

```powershell
powershell -ExecutionPolicy Bypass installer\build.ps1
```

הפלט / Output: `installer\HebrewMaps-Setup-1.0.0.exe`

---

## מה הסקריפט עושה (שלב אחר שלב) / What the script does

1. **מתקין PyInstaller ו-resvg** (`requirements-icons.txt`) בתוך ה-`.venv` של הפרויקט.
2. **מרנדר את כל האייקונים מקומית** מ-`web\icons\icon.svg` (`tools\make_icons.py`): `installer\icon.ico`, ‏`web\icons\*.png`, ‏`src-tauri\icons\*`. שום דבר לא מורד (נטפרי).
   Renders all icons locally from the SVG master — nothing is downloaded.
3. **מריץ PyInstaller** → `installer\dist\hebrew-maps\` (EXE + `_internal\`)
4. **בונה את אפליקציית שולחן העבודה** (`cargo build --release` ב-`src-tauri\`) ומעתיק את `hebrew-maps-desktop.exe` ל-`installer\dist\hebrew-maps\`. מדלגים עם `-SkipDesktop`, או אוטומטית כש-Rust לא מותקן.
   Builds the Tauri desktop exe and copies it next to `hebrew-maps.exe` (skipped with `-SkipDesktop` or when Rust is missing).
5. **מריץ Inno Setup** (`iscc.exe`) → `installer\HebrewMaps-Setup-1.0.0.exe`. אם `hebrew-maps-desktop.exe` קיים, קיצורי הדרך מפעילים אותו (כשיש WebView2). אחרת הם מפעילים את `hebrew-maps.exe`.

---

## אפליקציית שולחן העבודה / Desktop app (Tauri)

- `hebrew-maps-desktop.exe` מפעיל את `hebrew-maps.exe --no-browser --port N` מאותה תיקייה, מציג מסך טעינה עד ש-`/api/health` עונה, וטוען את `http://127.0.0.1:N/map.html` בחלון משלו.
- **פורט:** ‏48080, או הפורט האחרון שעבד. פורט תפוס מדולג, ואת השרת שמחזיק בו לא סוגרים. אפשר לקבוע פורט עם `HMAPS_DESKTOP_PORT`.
- **סגירה:** סגירת החלון עוצרת את השרת. השרת רץ בתוך Windows Job Object, ולכן הוא נסגר גם אם האפליקציה קורסת.
- **מצב פיתוח:** `set HMAPS_URL=http://127.0.0.1:8080/desktop.html` פותח את הכתובת בלי להפעיל שרת. `HMAPS_SERVER_EXE` מגדיר נתיב אחר ל-exe של השרת.
- **יומן:** `%LOCALAPPDATA%\il.hebrewmaps.desktop\logs\hebrew-maps-desktop.log`. יומן השרת נשאר `{app}\hebrew-maps.log`.
- **WebView2:** מותקן מראש ב-Windows 11. אם הוא חסר, המתקין יוצר קיצורי דרך ל-`hebrew-maps.exe` (דפדפן).

בנייה ידנית של האפליקציה בלבד / Build only the desktop exe:
```powershell
cargo build --release --locked --manifest-path src-tauri\Cargo.toml
copy src-tauri\target\release\hebrew-maps-desktop.exe installer\dist\hebrew-maps\
```
לא צריך tauri CLI או Node: התכונה `custom-protocol` מופעלת ב-`Cargo.toml`, ו-`bundle.active=false`, כי Inno Setup הוא שאורז את המתקין.
No tauri CLI/Node needed. If you have it, `cargo tauri build --no-bundle` works too.

תכנון ונימוקים / Design rationale: [docs/adr/0009-desktop-tauri.md](../docs/adr/0009-desktop-tauri.md)

---

## בנייה ידנית / Manual build steps

אם רוצים לבצע כל שלב בנפרד:

If you prefer to run each step separately:

```powershell
# 1. Install PyInstaller
.venv\Scripts\pip.exe install pyinstaller==6.10.0

# 2. Generate icons (from web\icons\icon.svg; needs requirements-icons.txt)
.venv\Scripts\pip.exe install -r requirements-icons.txt
.venv\Scripts\python.exe tools\make_icons.py

# 3. Run PyInstaller
.venv\Scripts\pyinstaller.exe installer\hebrew-maps.spec ^
    --distpath installer\dist --workpath installer\build --noconfirm

# 3b. Desktop app (optional, needs Rust)
cargo build --release --locked --manifest-path src-tauri\Cargo.toml
copy src-tauri\target\release\hebrew-maps-desktop.exe installer\dist\hebrew-maps\

# 4. Compile installer (requires Inno Setup 6 installed)
iscc installer\hebrew-maps.iss
```

---

## פרמטרים / Parameters

```powershell
# גרסה מותאמת אישית ונתיב מותאם ל-ISCC.exe
installer\build.ps1 -Version 1.2.0 -IsccPath "C:\Tools\InnoSetup\ISCC.exe"
```

בלי `-Version` נלקחת הגרסה מ-`hmaps\__init__.py`; עם `-Version` היא נכתבת קודם (`tools\set_version.py`) גם ל-`src-tauri\tauri.conf.json` ול-`Cargo.toml`, כך שהשרת, החלון והמתקין מציגים אותה גרסה.
Without `-Version` the version comes from `hmaps\__init__.py`; a given version is first written into it,
`tauri.conf.json` and `Cargo.toml` (`tools\set_version.py`) – one version everywhere.

### עדכון אוטומטי / Silent self-update
התוכנה המותקנת מעדכנת את עצמה (עדכונים ← "עדכון התוכנה", `hmaps/api/appupdate.py`): היא מורידה את המתקין של הגרסה האחרונה, בודקת SHA-256 מול `app-manifest.json` ומריצה:
The installed program updates itself: it downloads the newest installer, checks it against `app-manifest.json` and runs

```
HebrewMaps-Setup-1.2.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /NOCANCEL /LOG=<file> /AUTOUPDATE=1
```

‏`PrepareToInstall` סוגר את החלון ואת השרת, ו-`/AUTOUPDATE=1` (`{param:AUTOUPDATE}`) פותח את התוכנה מחדש בסוף. `data\` לא נדרס: קובצי נתונים מבנייה מקומית מסומנים `onlyifdoesntexist`.
`PrepareToInstall` closes the window and the server; `/AUTOUPDATE=1` starts the program again at the end. `data\` is never overwritten.

---

## מה כלול במתקין / What is included in the installer

- Python 3.12 + כל ספריות Python (מוטמע, ללא התקנה למשתמש)
  Python 3.12 + all Python libraries (embedded, no user install needed)
- קבצי Web — HTML, JS, CSS, גופנים, אייקונים (`web\`)
  Web files — HTML, JS, CSS, fonts, icons (`web\`)
- נתוני demo — `data\sample\maps.db`
  Demo dataset — `data\sample\maps.db`
- קבצי Tiles, ניווט, DEM, תחבורה ציבורית — אם קיימים ב-`data\`
  Base map tiles, map database, transit, and prayer times (public CI installer); the program-only installer contains no data snapshot

---

## Data included in the public installer

The Windows installer workflow downloads the current `maps`, `basemap`, `transit`, and `zmanim`
components before compiling the full `HebrewMaps-Setup-<version>.exe`. The maps component includes
the offline routing graph. The installer does not include terrain, media, or satellite photos. The
full installer is the file linked from the website.

The same release also contains `HebrewMaps-Setup-Program-<version>.exe`, a small program-only
installer. `app-manifest.json` points to this file, so in-app software updates do not redownload the
map data. Both installers preserve data already present on the target computer.

## התקנה / Installation

לחצו פעמיים על `HebrewMaps-Setup-1.0.0.exe` ועקבו אחרי האשף.

Double-click `HebrewMaps-Setup-1.0.0.exe` and follow the wizard.

מיקום ברירת המחדל / Default install location:
```
C:\Users\<user>\AppData\Local\Programs\HebrewMaps\
```

---

## הסרת התקנה / Uninstall

**לוח הבקרה** → **תוכניות** → **הסרת התקנת תוכנית** → בחרו **מפות ישראל**

**Control Panel** → **Programs** → **Uninstall a program** → select **מפות ישראל**

---

## מבנה הספריות לאחר ההתקנה / Installed directory layout

```
{app}\
  hebrew-maps-desktop.exe ← Desktop app (Tauri window; starts/stops the server) – shortcuts point here
  hebrew-maps.exe        ← Server (opens the browser when started on its own; --no-browser from the desktop app)
  _internal\             ← Python runtime + all libraries (PyInstaller)
  web\                   ← Web assets (HTML, JS, CSS, fonts)
  data\
    sample\maps.db       ← Demo database
    tiles\               ← Map tiles (if built)
    routing\             ← Routing graph (if built)
    dem\                 ← Elevation data (if built)
    transit\             ← Public transit GTFS (if built)
    media\               ← POI images (if downloaded)
    reports\             ← Quality reports (written at runtime)
  hebrew-maps.log        ← Application log (written at runtime)
```

---

## פתרון בעיות / Troubleshooting

| בעיה / Problem | פתרון / Solution |
|---|---|
| `"missing module name 'X'"` during PyInstaller | הוסיפו את `'X'` ל-`hiddenimports` ב-`installer\hebrew-maps.spec` / Add `'X'` to `hiddenimports` in the spec |
| `"Inno Setup not found"` | התקינו מ-https://jrsoftware.org/isdl.php והריצו עם `-IsccPath` / Install and rerun with `-IsccPath` |
| האפליקציה לא עולה — בדקו את הלוג / App doesn't start — check the log | פתחו `hebrew-maps.log` בתיקיית ההתקנה / Open `hebrew-maps.log` in the install folder |
| החלון נשאר על "טוען את המפה…" או מציג שגיאה / Window stuck loading or shows an error | בדקו את `hebrew-maps.log` ואת יומן האפליקציה (`%LOCALAPPDATA%\il.hebrewmaps.desktop\logs`) / Check both logs |
| קיצור הדרך פותח דפדפן ולא חלון / Shortcut opens the browser | WebView2 לא מותקן בזמן ההתקנה, או שהמתקין נבנה בלי Rust. התקינו WebView2 Runtime והתקינו מחדש / WebView2 was missing at install time or the installer was built without Rust |
| `"settings reads wrong paths"` | ודאו ש-`HMAPS_DATA` ו-`HMAPS_WEB` מוגדרים לפני import של `hmaps.api.app` / Ensure vars are set before `hmaps.api.app` is imported |

---

## הערות מפתח / Developer notes

- **AppId GUID** ב-`hebrew-maps.iss` חייב להישאר קבוע לאורך כל הגרסאות כדי שהמתקין יזהה שדרוגים.
  The **AppId GUID** in `hebrew-maps.iss` must never change across versions for upgrade detection to work.
- ספריית `data\` **אינה** נמחקת בעת הסרת ההתקנה — נתוני המשתמש נשמרים.
  The `data\` directory is **not** removed on uninstall — user data is preserved.
- `installer\dist\` ו-`installer\build\` נוספו ל-`.gitignore` ויש להוסיפם אם לא קיימים.
  `installer\dist\` and `installer\build\` should be in `.gitignore`.
