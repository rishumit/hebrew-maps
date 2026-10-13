# בניית מתקין Windows — מפות בעברית
# Windows Installer Build Guide — Hebrew Maps

---

## דרישות מקדימות / Prerequisites

| כלי / Tool | גרסה / Version | הורדה / Download |
|---|---|---|
| Windows | 10/11 x64 | — |
| Python 3.12 + `.venv` | כלול בפרויקט / Included | הריצו `setup-windows.cmd` |
| Inno Setup 6 | 6.x | https://jrsoftware.org/isinfo.php |

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

1. **מתקין PyInstaller 6.10** בתוך ה-`.venv` של הפרויקט.
2. **יוצר `installer\icon.ico`** מתמונת PNG מ-`web\icons\` (או כחול מוצק כגיבוי).
3. **מריץ PyInstaller** → `installer\dist\hebrew-maps\` (EXE + `_internal\`)
4. **מריץ Inno Setup** (`iscc.exe`) → `installer\HebrewMaps-Setup-1.0.0.exe`

---

## בנייה ידנית / Manual build steps

אם רוצים לבצע כל שלב בנפרד:

If you prefer to run each step separately:

```powershell
# 1. Install PyInstaller
.venv\Scripts\pip.exe install pyinstaller==6.10.0

# 2. Generate icon
.venv\Scripts\python.exe installer\make_icon.py

# 3. Run PyInstaller
.venv\Scripts\pyinstaller.exe installer\hebrew-maps.spec ^
    --distpath installer\dist --workpath installer\build --noconfirm

# 4. Compile installer (requires Inno Setup 6 installed)
iscc installer\hebrew-maps.iss
```

---

## פרמטרים / Parameters

```powershell
# גרסה מותאמת אישית ונתיב מותאם ל-ISCC.exe
installer\build.ps1 -Version 1.2.0 -IsccPath "C:\Tools\InnoSetup\ISCC.exe"
```

---

## מה כלול במתקין / What is included in the installer

- Python 3.12 + כל ספריות Python (מוטמע, ללא התקנה למשתמש)
  Python 3.12 + all Python libraries (embedded, no user install needed)
- קבצי Web — HTML, JS, CSS, גופנים, אייקונים (`web\`)
  Web files — HTML, JS, CSS, fonts, icons (`web\`)
- נתוני demo — `data\sample\maps.db`
  Demo dataset — `data\sample\maps.db`
- קבצי Tiles, ניווט, DEM, תחבורה ציבורית — אם קיימים ב-`data\`
  Tiles, routing, DEM, transit data — if present in `data\`

---

## התקנה / Installation

לחצו פעמיים על `HebrewMaps-Setup-1.0.0.exe` ועקבו אחרי האשף.

Double-click `HebrewMaps-Setup-1.0.0.exe` and follow the wizard.

מיקום ברירת המחדל / Default install location:
```
C:\Users\<user>\AppData\Local\Programs\HebrewMaps\
```

---

## הסרת התקנה / Uninstall

**לוח הבקרה** → **תוכניות** → **הסרת התקנת תוכנית** → בחרו **מפות בעברית**

**Control Panel** → **Programs** → **Uninstall a program** → select **מפות בעברית**

---

## מבנה הספריות לאחר ההתקנה / Installed directory layout

```
{app}\
  hebrew-maps.exe        ← Main executable (opens browser automatically)
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
| `"settings reads wrong paths"` | ודאו ש-`HMAPS_DATA` ו-`HMAPS_WEB` מוגדרים לפני import של `hmaps.api.app` / Ensure vars are set before `hmaps.api.app` is imported |

---

## הערות מפתח / Developer notes

- **AppId GUID** ב-`hebrew-maps.iss` חייב להישאר קבוע לאורך כל הגרסאות כדי שהמתקין יזהה שדרוגים.
  The **AppId GUID** in `hebrew-maps.iss` must never change across versions for upgrade detection to work.
- ספריית `data\` **אינה** נמחקת בעת הסרת ההתקנה — נתוני המשתמש נשמרים.
  The `data\` directory is **not** removed on uninstall — user data is preserved.
- `installer\dist\` ו-`installer\build\` נוספו ל-`.gitignore` ויש להוסיפם אם לא קיימים.
  `installer\dist\` and `installer\build\` should be in `.gitignore`.
