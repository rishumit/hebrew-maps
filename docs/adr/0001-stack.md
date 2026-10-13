# ADR 0001 – טכנולוגיות: Python/Starlette בשרת, JavaScript ללא שלב build בדפדפן

**סטטוס:** התקבל · אוקטובר 2026

## הקשר
במסמכים נשארו פתוחות השאלות React/Vue ו-Node/Python (01-requirements §8, docs/README). המערכת צריכה לרוץ על מחשב Windows אחד עם Docker Desktop, ולהיות פשוטה לתחזוקה גם בלי צוות פיתוח קבוע.

## החלטה
- **שרת:** Python 3.13 עם Starlette (ASGI) ו-uvicorn. Starlette היא התשתית ש-FastAPI בנויה עליה, בלי שכבת התלות הנוספת. צינור הנתונים כתוב באותה שפה, כך שקוד העברית (נרמול, קטגוריות, שעות פתיחה) משותף לשרת ולצינור.
- **לקוח:** ‏HTML, ‏CSS ומודולי ES בלי שלב build. ‏MapLibre GL JS (קוד פתוח, ללא מפתח API) משמש להצגת אריחים וקטוריים, עם תוסף RTL לעברית.
- רק ספריות צד שלישי מעטות: starlette, ‏uvicorn ו-numpy בשרת; ‏maplibre-gl ותוסף ה-RTL בלקוח.

## השלכות
- אין `node_modules` בפרויקט ואין צורך בשלב build. ‏Docker מוריד את MapLibre בזמן בניית האימג'.
- ממשק בלי framework דורש משמעת: רכיבים קטנים (`combobox`, ‏`panel`, ‏`views`) ותבנית HTML עם escape אוטומטי (`util.html`).
