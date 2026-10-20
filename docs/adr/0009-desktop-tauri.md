# ADR 0009 – אפליקציית שולחן עבודה (Tauri) ואייקונים שנוצרים מקומית

**סטטוס:** התקבל · אוקטובר 2026

## הקשר
עד עכשיו `hebrew-maps.exe` (‏PyInstaller, ‏[ADR 0006](0006-native-mode.md)) הפעיל את השרת ופתח את הדפדפן בכתובת `http://localhost:8080`. זה עבד, אבל המשתמשים קיבלו לשונית דפדפן במקום אפליקציה: אין חלון ייעודי, סגירת הלשונית לא עוצרת את השרת, והשרת נשאר רץ ברקע בלי דרך פשוטה לסגור אותו. בנוסף, האייקונים הוכנו ידנית או הורדו, ובמחשבים עם סינון נטפרי תמונות חיצוניות נחסמות.

## החלטה

### חלון שולחן עבודה – Tauri 2
- **מעטפת דקה ב-Rust** (`src-tauri/`, ‏`hebrew-maps-desktop.exe`). הממשק עצמו לא משתנה: החלון טוען את אותו `map.html` מהשרת המקומי. ב-Windows החלון משתמש ב-WebView2 (Edge), שמותקן מראש ב-Windows 11 וב-Windows 10 מעודכן.
- **השרת נשאר תהליך נפרד.** תיקיית ה-onedir של PyInstaller (‏exe + ‏`_internal\`) לא מתאימה ל-`externalBin` של Tauri, שמיועד לקובץ בודד. לכן Inno Setup מתקין את `hebrew-maps-desktop.exe` **באותה תיקייה** כמו `hebrew-maps.exe`, ‏`web\` ו-`data\`, והאפליקציה מפעילה את השרת עם `std::process::Command`. לא נעשה שימוש ב-resources של Tauri ולא ב-bundler שלה (`bundle.active = false`). מבנה ההתקנה נשאר כמו שהיה, ו-`hebrew-maps.exe` עדיין עובד גם לבד.
- **הפעלה:** ‏`hebrew-maps.exe --no-browser --host 127.0.0.1 --port N`, יחד עם אותם ערכים במשתני סביבה (`HMAPS_NO_BROWSER`, ‏`HMAPS_PORT`) לטובת גרסאות שרת ישנות. התהליך רץ עם `CREATE_NO_WINDOW`. בזמן ההמתנה מוצג מסך טעינה מקומי בעברית (`src-tauri/ui/index.html`), עד ש-`/api/health` עונה. תשובה 200 או 503 (‏"degraded", למשל כשאין עדיין נתונים) נחשבת מוכנה, בתנאי שהגוף הוא JSON של hmaps ושהתהליך שלנו עדיין חי. רק אז החלון עובר ל-`http://127.0.0.1:N/map.html`.
- **פורט קבוע:** ‏48080 כברירת מחדל, והפורט האחרון שעבד נשמר. פורט קבוע שומר על אותו origin, וכך ה-localStorage (היסטוריה, הגדרות) ומטמון ה-service worker נשמרים בין הפעלות. פורט תפוס (למשל שרת של התקנה ישנה, או של `start-windows.cmd`) לא נסגר ולא נעשה בו שימוש: האפליקציה עוברת לפורט הבא (עד 20 ניסיונות). אם השרת נסגר מיד, האפליקציה מנסה שוב על הפורט הבא, ואחרי 3 כישלונות מוצג מסך שגיאה עם נתיב ליומן.
- **עצירה:** סגירת החלון הורגת את השרת. ב-Windows השרת משויך ל-**Job Object** עם `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. ה-handle היחיד נמצא בתהליך האפליקציה, ולכן גם קריסה או "סיום משימה" סוגרים את השרת ואת כל תהליכי הבן שלו. ב-Linux (פיתוח) משתמשים ב-`PR_SET_PDEATHSIG`.
- **מופע יחיד:** ‏`tauri-plugin-single-instance`. הפעלה שנייה רק מביאה לחזית את החלון הקיים.
- **גודל ומיקום החלון** נשמרים עם `tauri-plugin-window-state`. גודל מינימלי 640×480, וכותרת "מפות בעברית". כיוון RTL מגיע מהדף (`dir="rtl"`).
- **קישורים חיצוניים** (כל http/https שאינו localhost, וגם mailto/tel) נפתחים בדפדפן של המערכת, דרך `on_navigation` ו-`on_new_window`. קישורים מקומיים בחלון חדש (כמו דוח האיכות) נפתחים בחלון של האפליקציה.
- **אבטחה ופרטיות:** הדף המרוחק לא מקבל גישה ל-IPC של Tauri (אין capabilities, ‏`withGlobalTauri=false`). אין טלמטריה ואין plugin עדכונים, כי העדכונים מטופלים בפיצ'ר נפרד.
- **מצב פיתוח:** כשמוגדר `HMAPS_URL`, האפליקציה רק פותחת את הכתובת ולא מפעילה שרת. כך `start-windows.cmd` מציג בחלון את שרת ה-venv: הוא מפעיל את השרת כרגיל, ואם נמצא `hebrew-maps-desktop.exe` (נבנה ב-`src-tauri\target\release`, או מותקן) הוא נפתח עם `HMAPS_URL`. אם לא, נפתח הדפדפן. `HMAPS_BROWSER=1` מכריח דפדפן.
- **בנייה:** ‏`cargo build --release` בלבד. התכונה `custom-protocol` של tauri מופעלת ב-`Cargo.toml`, ולכן אין צורך ב-tauri CLI או ב-Node. `build.ps1` וה-CI מעתיקים את ה-exe ל-`installer\dist\hebrew-maps\`, ו-Inno Setup מזהה אותו בזמן הקומפילציה (`#if FileExists`). אם Rust לא מותקן, ה-exe לא נבנה, והמתקין עובד כמו קודם (דפדפן).
- **WebView2 חסר:** המתקין בודק ברישום (`EdgeUpdate\Clients\{F3017226-…}`). אם אין WebView2, קיצורי הדרך מפעילים את `hebrew-maps.exe` (דפדפן). המתקין לא מוריד את WebView2.

### אייקונים – רק מ-`web/icons/icon.svg`
- `tools/make_icons.py` מרנדר את קובץ ה-SVG הראשי בעזרת **resvg** (חבילת `resvg-py`: wheel בינארי ל-Windows, ל-Linux ול-macOS, בלי Cairo/GTK). הגרסה נעוצה ב-`requirements-icons.txt`. כגיבוי משמש Chromium של Playwright.
- **פלטים:**
  - `web/icons/icon-192.png`, ‏`icon-512.png`
  - `icon-maskable-512.png`: הרקע נמתח עד הקצוות, והציור מוקטן ל-76% כדי להיכנס לאזור הבטוח (עיגול של 40%)
  - `installer/icon.ico` (‏16–256, לא נשמר ב-git)
  - `src-tauri/icons/`: ‏32, 128, ‏128@2x, ‏icon.png, ‏icon.ico
- **שום דבר לא מורד** (נטפרי חוסם תמונות חיצוניות). קובצי ה-PNG נשמרים ב-git, כדי ש-`cargo build` ו-PWA יעבדו גם בלי Python. הסקריפט אידמפוטנטי וכותב קובץ רק אם התוכן השתנה. `--check` מדווח על קבצים לא מעודכנים. `build.ps1` וה-CI מריצים אותו לפני PyInstaller.

## השלכות
- המתקין גדל בכמה MB (ה-exe של Tauri). ב-Windows 11 לא צריך להתקין שום דבר נוסף.
- ה-origin של האפליקציה (`127.0.0.1:48080`) שונה מזה של הדפדפן (`localhost:8080`), ולכן היסטוריית החיפושים וההגדרות לא משותפות ביניהם.
- נתוני WebView2 וקובץ מצב החלון נשמרים ב-`%LOCALAPPDATA%\il.hebrewmaps.desktop` וב-`%APPDATA%\il.hebrewmaps.desktop`. יומן האפליקציה נמצא ב-`%LOCALAPPDATA%\il.hebrewmaps.desktop\logs\hebrew-maps-desktop.log`, ויומן השרת נשאר `hebrew-maps.log` בתיקיית ההתקנה. הסרת ההתקנה לא מוחקת אותם.
- גרסת ה-exe של Tauri קבועה ב-`tauri.conf.json` (‏1.0.0), ולא נגזרת מתג הגרסה של המתקין.
