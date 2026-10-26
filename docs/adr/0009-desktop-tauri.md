# ADR 0009 – אפליקציית שולחן עבודה (Tauri) ואייקונים שנוצרים מקומית

**סטטוס:** התקבל · אוקטובר 2026

## הקשר
עד עכשיו `hebrew-maps.exe` (‏PyInstaller, ‏[ADR 0006](0006-native-mode.md)) הפעיל את השרת ופתח את הדפדפן בכתובת `http://localhost:8080`. זה עבד, אבל המשתמשים קיבלו לשונית דפדפן במקום אפליקציה: אין חלון ייעודי, סגירת הלשונית לא עוצרת את השרת, והשרת נשאר רץ ברקע בלי דרך פשוטה לסגור אותו. בנוסף, האייקונים הוכנו ידנית או הורדו, ובמחשבים עם סינון נטפרי תמונות חיצוניות נחסמות.

## החלטה

### חלון שולחן עבודה – Tauri 2
- **מעטפת דקה ב-Rust** (`src-tauri/`, ‏`hebrew-maps-desktop.exe`). המפה עצמה לא משתנה: החלון טוען את `desktop.html` מהשרת המקומי, ובו כל לשונית היא `map.html` ב-iframe (ראו "שורת כותרת ולשוניות" למטה). ב-Windows החלון משתמש ב-WebView2 (Edge), שמותקן מראש ב-Windows 11 וב-Windows 10 מעודכן.
- **השרת נשאר תהליך נפרד.** תיקיית ה-onedir של PyInstaller (‏exe + ‏`_internal\`) לא מתאימה ל-`externalBin` של Tauri, שמיועד לקובץ בודד. לכן Inno Setup מתקין את `hebrew-maps-desktop.exe` **באותה תיקייה** כמו `hebrew-maps.exe`, ‏`web\` ו-`data\`, והאפליקציה מפעילה את השרת עם `std::process::Command`. לא נעשה שימוש ב-resources של Tauri ולא ב-bundler שלה (`bundle.active = false`). מבנה ההתקנה נשאר כמו שהיה, ו-`hebrew-maps.exe` עדיין עובד גם לבד.
- **הפעלה:** ‏`hebrew-maps.exe --no-browser --host 127.0.0.1 --port N`, יחד עם אותם ערכים במשתני סביבה (`HMAPS_NO_BROWSER`, ‏`HMAPS_PORT`) לטובת גרסאות שרת ישנות. התהליך רץ עם `CREATE_NO_WINDOW`. בזמן ההמתנה מוצג מסך טעינה מקומי בעברית (`src-tauri/ui/index.html`), עד ש-`/api/health` עונה. תשובה 200 או 503 (‏"degraded", למשל כשאין עדיין נתונים) נחשבת מוכנה, בתנאי שהגוף הוא JSON של hmaps ושהתהליך שלנו עדיין חי. רק אז החלון עובר ל-`http://127.0.0.1:N/desktop.html`.
- **פורט קבוע:** ‏48080 כברירת מחדל, והפורט האחרון שעבד נשמר. פורט קבוע שומר על אותו origin, וכך ה-localStorage (היסטוריה, הגדרות) ומטמון ה-service worker נשמרים בין הפעלות. פורט תפוס (למשל שרת של התקנה ישנה, או של `start-windows.cmd`) לא נסגר ולא נעשה בו שימוש: האפליקציה עוברת לפורט הבא (עד 20 ניסיונות). אם השרת נסגר מיד, האפליקציה מנסה שוב על הפורט הבא, ואחרי 3 כישלונות מוצג מסך שגיאה עם נתיב ליומן.
- **עצירה:** סגירת החלון הורגת את השרת. ב-Windows השרת משויך ל-**Job Object** עם `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. ה-handle היחיד נמצא בתהליך האפליקציה, ולכן גם קריסה או "סיום משימה" סוגרים את השרת ואת כל תהליכי הבן שלו. ב-Linux (פיתוח) משתמשים ב-`PR_SET_PDEATHSIG`.
- **מופע יחיד:** ‏`tauri-plugin-single-instance`. הפעלה שנייה רק מביאה לחזית את החלון הקיים.
- **גודל ומיקום החלון** נשמרים עם `tauri-plugin-window-state`. גודל מינימלי 640×480, וכותרת "מפות ישראל". כיוון RTL מגיע מהדף (`dir="rtl"`).
- **קישורים חיצוניים** (כל http/https שאינו localhost, וגם mailto/tel) נפתחים בדפדפן של המערכת, דרך `on_navigation` ו-`on_new_window`. קישורים מקומיים בחלון חדש (כמו דוח האיכות) נפתחים בחלון של האפליקציה.
- **אבטחה ופרטיות:** הדפים לא מקבלים שום API של Tauri מלבד פקודות החלון של שורת הכותרת (ראו למטה). ‏`withGlobalTauri=false`. אין טלמטריה ואין plugin עדכונים, כי העדכונים מטופלים בפיצ'ר נפרד.
- **מצב פיתוח:** כשמוגדר `HMAPS_URL`, האפליקציה רק פותחת את הכתובת ולא מפעילה שרת. כתובת של `…/map.html#…` נפתחת כ-`…/desktop.html#…`, כי בלי מסגרת חלון צריך את שורת הכותרת שלנו. כך `start-windows.cmd` מציג בחלון את שרת ה-venv: הוא מפעיל את השרת כרגיל, ואם נמצא `hebrew-maps-desktop.exe` (נבנה ב-`src-tauri\target\release`, או מותקן) הוא נפתח עם `HMAPS_URL`. אם לא, נפתח הדפדפן. `HMAPS_BROWSER=1` מכריח דפדפן.
- **בנייה:** ‏`cargo build --release` בלבד. התכונה `custom-protocol` של tauri מופעלת ב-`Cargo.toml`, ולכן אין צורך ב-tauri CLI או ב-Node. `build.ps1` וה-CI מעתיקים את ה-exe ל-`installer\dist\hebrew-maps\`, ו-Inno Setup מזהה אותו בזמן הקומפילציה (`#if FileExists`). אם Rust לא מותקן, ה-exe לא נבנה, והמתקין עובד כמו קודם (דפדפן).
- **WebView2 חסר:** המתקין בודק ברישום (`EdgeUpdate\Clients\{F3017226-…}`). אם אין WebView2, קיצורי הדרך מפעילים את `hebrew-maps.exe` (דפדפן). המתקין לא מוריד את WebView2.

### שורת כותרת ולשוניות (אוקטובר 2026)
- **בלי מסגרת Windows** (`decorations: false`, ‏`shadow: true`). ‏Windows 11 שומר על הצל ועל הפינות המעוגלות, ו-Tauri משאיר את גבולות שינוי הגודל בחלון בלי מסגרת. מצב החלון שנשמר לא משחזר `decorations` (‏`StateFlags::DECORATIONS` מוחרג), כי גרסה קודמת שמרה `true`.
- **שורת כותרת RTL בגובה 40px:** הסמל והשם מימין, הלשוניות ו-"+" אחריהם, אזור גרירה ריק, וכפתורי החלון **משמאל** בסדר הפוך (סגירה בקצה השמאלי), כמו באפליקציות Windows בעברית. כפתורים של 46px עם צבעי ריחוף של Windows 11 (סגירה: ‏`#C42B1C`), מצב חלון לא פעיל מעומעם, ערכת צבעים בהירה/כהה לפי "מראה הממשק" של המפה (‏localStorage משותף, אירוע `storage`). הגרירה משתמשת ב-`data-tauri-drag-region` של Tauri (גרירה, ולחיצה כפולה מגדילה). מסך הטעינה והשגיאה (`ui/index.html`) מציג אותה שורה עם כפתורי מזעור/הגדלה/סגירה, אחרת לא היה אפשר לסגור את החלון.
- **לשוניות = iframe לכל מפה** בדף מעטפת שהשרת מגיש: `web/desktop.html`, ‏`js/desktop.js`, ‏`css/desktop.css`. ה-iframe באותו origin, ולכן `X-Frame-Options: SAMEORIGIN` ו-`frame-ancestors 'self'` נשארים כמו שהם. החלופה, חלון WebView לכל לשונית או webviews מרובים ב-Rust, דורשת API לא יציב (`unstable`) ולוגיקת פריסה ב-Rust. iframe עובד באותה צורה גם בדפדפן רגיל, ונבדק ב-Playwright.
  - **מצב כל לשונית הוא ה-hash של המפה** (`#map=…&q=…|item=…|dir=…`). ‏`app.js` שולח `{type:'hmaps:state', title, kind, hash}` עם `postMessage` (‏targetOrigin = ה-origin שלנו) כשמשתנים החלונית או ה-hash, וגם מעדכן `document.title` ("דיזנגוף סנטר – מפות ישראל"), שימושי גם בדפדפן. הכותרת: טקסט החיפוש, שם המקום, "א ← ב" למסלול, או "מפה". ‏Rust מעביר את `document.title` של המעטפת לכותרת החלון (שורת המשימות, Alt+Tab) עם `on_document_title_changed`, בלי הרשאת `set_title`.
  - **שמירה ושחזור:** הרשימה (hash, כותרת, סוג) נשמרת ב-`localStorage['hmaps:desktop-tabs']`. בהפעלה הבאה רק הלשונית הפעילה נטענת, והשאר נטענות בפעם הראשונה שעוברים אליהן.
  - **זיכרון:** לשוניות ברקע נשארות טעונות (`visibility:hidden` ו-`inert`, אותו גודל, כך שהמעבר מיידי ו-MapLibre לא צריך resize). עד 8 מפות טעונות בבת אחת. כל מפה מחזיקה הקשר WebGL, ול-Chromium יש מגבלה של כ-16 הקשרים. הלשונית שלא הייתה בשימוש הכי הרבה זמן נפרקת, ונטענת מחדש מה-hash שלה.
  - **קישורים:** קישור למפה מקומית עם `target=_blank`, ‏Ctrl+קליק או לחיצה באמצעי נפתח בלשונית חדשה. המעטפת מיירטת אותו בתוך ה-iframe. ‏`window.open` מגיע ל-`on_new_window` ב-Rust, וזה קורא ל-`hmapsDesktop.openUrl(<מחרוזת JSON>)`. קישורים חיצוניים ממשיכים לדפדפן המערכת. ‏`desktop.html#map=…` פותח את המפה הזו בלשונית.
  - **מקלדת ונגישות:** ‏`role=tablist/tab/tabpanel`, ‏roving tabindex, חצים לפי RTL (שמאלה = הלשונית הבאה), ‏Home/End/Delete. קיצורים: ‏Ctrl+T/W/Shift+T/Tab/Shift+Tab/PageUp/PageDown/1…9, וגם Ctrl+F4. הם נתפסים גם בתוך ה-iframe, כי המעטפת מאזינה ל-keydown של כל מפה. כשיש הרבה לשוניות הן מתכווצות (בלי כותרת מתחת ל-92px, וכפתור הסגירה של לשוניות ברקע נעלם מתחת ל-64px), ואז הרצועה נגללת (גם בגלגלת). סגירת הלשונית האחרונה משאירה מפה חדשה. סגירת האפליקציה היא בכפתור ✕ של החלון או ב-Alt+F4.
- **הרשאות (least privilege):** הדף מרוחק (`http://127.0.0.1:N`), ולכן בלי capability מפורשת אין לו גישה ל-IPC. מותרות רק פקודות החלון של Tauri עצמו, ולא פקודות Rust משלנו: `core:window:allow-minimize`, ‏`allow-toggle-maximize`, ‏`allow-internal-toggle-maximize` (לחיצה כפולה על אזור הגרירה), ‏`allow-close`, ‏`allow-start-dragging`, ‏`allow-is-maximized`. אין events, ‏set-title, קבצים או shell.
  - לדף המקומי (מסך הטעינה): `capabilities/window-controls-local.json` (‏`local: true`, חלון `main`).
  - לשרת: capability שנוספת **בזמן ריצה** (`add_capability`) רק אחרי שהשרת שלנו עבר את בדיקת הבריאות, ל-origin המדויק `http://127.0.0.1:<port>/*` ולחלון `main` בלבד. כך שרת אחר ב-localhost, או פורט אחר, לא מקבל אותן.
  - הקריאה עוברת דרך `window.__TAURI_INTERNALS__.invoke('plugin:window|…')`, ש-Tauri מזריק תמיד. ה-ACL הוא מה שמגביל. ל-`desktop.html` (רק לו) ה-CSP מוסיף `connect-src ipc: http://ipc.localhost`, ערוץ ה-IPC של Tauri (‏WebView2 משתמש ב-`http://ipc.localhost`).
- **service worker:** ה-iframes רושמים את `sw.js` עם scope ‏`/`, ולכן הוא מגיש גם את המעטפת. שלושת הקבצים נוספו ל-SHELL (גרסה 1.5.0).
- **לא נבדק ב-Windows אמיתי** (נבדק ב-Linux/WebKitGTK תחת Xvfb ו-openbox: מזעור, הגדלה ושחזור, סגירה, גרירה, Ctrl+T/Tab/1, סנכרון כותרת החלון, מסך שגיאה): פינות מעוגלות וצל, גבולות שינוי גודל, מצב מוגדל (ש-Windows לא מסתיר את שורת המשימות), לחיצה כפולה להגדלה (ב-Linux מנהל החלונות תופס את הלחיצה השנייה, ב-Windows Tauri מטפל בזה ב-HTCAPTION), Aero Snap בגרירה לקצה. **Snap Layouts** (תפריט הפריסות במעבר על כפתור ההגדלה ב-Windows 11) לא נתמך: הוא דורש ש-`WM_NCHITTEST` יחזיר `HTMAXBUTTON` לכפתור שמצויר ב-HTML. שיפור עתידי אפשרי עם subclass של החלון ב-Rust.

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
