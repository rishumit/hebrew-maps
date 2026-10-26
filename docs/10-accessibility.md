# מדריך נגישות - WCAG 2.1 AA Compliance

## 1. סקירה כללית

**תקן:** WCAG 2.1 Level AA  
**מטרה:** מערכת נגישה לכולם, כולל אנשים עם מוגבלויות

### 1.1 קבוצות יעד
- ליקויי ראייה (עיוורים, לקויי ראייה)
- ליקויי שמיעה
- מוגבלויות מוטוריות
- קשיי למידה / קוגניטיביים
- משתמשים מבוגרים

---

## 2. עקרונות WCAG (POUR)

### 2.1 Perceivable (ניתן לתפיסה)
**המידע חייב להיות ניתן לתפיסה על ידי כל המשתמשים**

✅ Alt text לכל התמונות  
✅ Contrast ratio מינימלי  
✅ גופנים קריאים  
✅ תמיכה בהגדלת טקסט

### 2.2 Operable (ניתן להפעלה)
**הממשק חייב להיות ניתן להפעלה בדרכים שונות**

✅ ניווט מקלדת מלא  
✅ Focus indicators ברורים  
✅ אין מלכודות מקלדת  
✅ זמן מספיק לבצע פעולות

### 2.3 Understandable (מובן)
**המידע והממשק חייבים להיות מובנים**

✅ שפה ברורה ופשוטה  
✅ הודעות שגיאה מפורטות  
✅ עקביות בממשק  
✅ עזרה וסיוע זמינים

### 2.4 Robust (עמיד)
**התוכן חייב לעבוד עם טכנולוגיות מסייעות**

✅ HTML סמנטי  
✅ ARIA attributes נכונים  
✅ עובד עם screen readers  
✅ תואם סטנדרטים

---

## 3. דרישות ספציפיות לפי קטגוריה

### 3.1 תמונות ותוכן חזותי

#### Alt Text (1.1.1)
```html
<!-- טוב ✅ -->
<img src="tower-of-david.jpg" alt="מגדל דוד בירושלים">

<!-- רע ❌ -->
<img src="tower-of-david.jpg" alt="image123">
```

**כללים:**
- תמונות מידע: alt מתאר את התוכן
- תמונות דקורטיביות: `alt=""`
- אייקונים פונקציונליים: alt מתאר את הפעולה

#### Icons with Text
```html
<!-- עם alt ייעודי -->
<button>
  <svg aria-label="חיפוש" role="img">...</svg>
  <span class="sr-only">חיפוש</span>
</button>
```

---

### 3.2 ניגודיות צבע (Contrast)

#### דרישות (1.4.3)
| סוג טקסט | Contrast Ratio מינימלי |
|----------|------------------------|
| טקסט רגיל (< 18px) | **4.5:1** |
| טקסט גדול (≥ 18px / ≥ 14px bold) | **3:1** |
| רכיבי UI (כפתורים, שדות) | **3:1** |
| גרפיקה מידע (charts) | **3:1** |

#### בדיקה
```css
/* טוב ✅ - contrast ratio: 7.8:1 */
color: #1F2937; /* gray-800 */
background: #FFFFFF;

/* גבולי ❌ - contrast ratio: 2.9:1 */
color: #9CA3AF; /* gray-400 */
background: #FFFFFF;
```

**כלים לבדיקה:**
- Chrome DevTools Lighthouse
- Contrast Checker (WebAIM)
- Figma plugins

---

### 3.3 ניווט מקלדת (Keyboard Navigation)

#### תמיכה מלאה (2.1.1)
**כל פעולה חייבת להיות ניתנת לביצוע גם עם מקלדת**

**מקשים חשובים:**
- `Tab` / `Shift+Tab` - מעבר בין רכיבים
- `Enter` / `Space` - הפעלת כפתור
- `Esc` - סגירת modal
- `Arrow keys` - ניווט בתפריטים

#### דוגמה - Search Autocomplete
```jsx
<input
  type="text"
  role="combobox"
  aria-expanded={showSuggestions}
  aria-controls="suggestions-list"
  aria-activedescendant={selectedId}
  onKeyDown={(e) => {
    if (e.key === 'ArrowDown') {
      // Move to next suggestion
    } else if (e.key === 'ArrowUp') {
      // Move to previous
    } else if (e.key === 'Enter') {
      // Select current
    } else if (e.key === 'Escape') {
      // Close suggestions
    }
  }}
/>
```

#### Focus Management
```css
/* ברור ✅ */
button:focus-visible {
  outline: 3px solid #2563EB;
  outline-offset: 2px;
}

/* לא מספיק ❌ */
button:focus {
  outline: none;
}
```

**אף פעם לא:** `outline: none` בלי חלופה!

---

### 3.4 כותרות והיררכיה (Headings)

#### Structure (1.3.1)
```html
<!-- טוב ✅ -->
<h1>מפות ישראל</h1>
  <h2>תוצאות חיפוש</h2>
    <h3>מסעדת השף</h3>
    <h3>בית קפה נורדאו</h3>
  <h2>קטגוריות</h2>
    <h3>מזון</h3>
    <h3>תיירות</h3>

<!-- רע ❌ - דילוג ברמות -->
<h1>מפות ישראל</h1>
<h3>תוצאות</h3>
```

**כללים:**
- `<h1>` פעם אחת בעמוד
- אין לדלג רמות (h1 → h3)
- שימוש סמנטי, לא רק עיצובי

---

### 3.5 טפסים ושדות קלט (Forms)

#### Labels (3.3.2)
```html
<!-- טוב ✅ -->
<label for="search-input">חיפוש מיקום</label>
<input
  id="search-input"
  type="text"
  aria-describedby="search-hint"
/>
<p id="search-hint">הקלד שם עיר, רחוב או מקום</p>

<!-- רע ❌ - אין label -->
<input type="text" placeholder="חיפוש מיקום">
```

#### Error Messages (3.3.1)
```html
<label for="address">כתובת</label>
<input
  id="address"
  type="text"
  aria-invalid="true"
  aria-describedby="address-error"
/>
<p id="address-error" role="alert">
  ⚠️ נא להזין כתובת תקינה
</p>
```

**הודעת שגיאה טובה:**
- ברורה ומפורטת
- מציעה פתרון
- נקראת על ידי screen reader

---

### 3.6 ARIA (Accessible Rich Internet Applications)

#### Roles
```html
<!-- Map container -->
<div
  role="application"
  aria-label="מפה אינטראקטיבית של ישראל"
>
  <!-- Map content -->
</div>

<!-- Search -->
<div role="search">
  <input type="text" aria-label="חיפוש מיקום" />
</div>

<!-- Navigation -->
<nav aria-label="תפריט ראשי">
  <ul>...</ul>
</nav>
```

#### States
```html
<!-- Button loading state -->
<button
  aria-busy="true"
  aria-label="טוען תוצאות חיפוש"
>
  <span class="spinner" aria-hidden="true"></span>
  טוען...
</button>

<!-- Toggle button -->
<button
  aria-pressed="true"
  aria-label="הצג מסעדות"
>
  🍴 מסעדות
</button>

<!-- Expandable section -->
<button
  aria-expanded="false"
  aria-controls="filter-panel"
>
  סינונים
</button>
<div id="filter-panel" hidden>
  <!-- Filters -->
</div>
```

#### Live Regions
```html
<!-- הודעה דינמית -->
<div
  role="status"
  aria-live="polite"
  aria-atomic="true"
>
  נמצאו 47 תוצאות
</div>

<!-- התראה דחופה -->
<div
  role="alert"
  aria-live="assertive"
>
  ⚠️ החיבור לשרת נכשל
</div>
```

---

### 3.7 POI Info Card - דוגמה מלאה

```html
<article
  role="dialog"
  aria-labelledby="poi-name"
  aria-describedby="poi-description"
  tabindex="-1"
>
  <!-- Close button -->
  <button
    class="close-btn"
    aria-label="סגור כרטיס מידע"
    onclick="closeCard()"
  >
    ✕
  </button>
  
  <!-- Image -->
  <img
    src="restaurant.jpg"
    alt="מסעדת השף - אווירה חמימה עם שולחנות עץ"
  />
  
  <!-- Content -->
  <h2 id="poi-name">מסעדת השף</h2>
  
  <div id="poi-description">
    <p>מסעדה איטלקית בלב תל אביב</p>
  </div>
  
  <!-- Rating -->
  <div aria-label="דירוג 4.5 מתוך 5 כוכבים">
    <span aria-hidden="true">⭐⭐⭐⭐⭐</span>
    <span class="sr-only">4.5 מתוך 5 כוכבים</span>
    <span>(120 ביקורות)</span>
  </div>
  
  <!-- Address -->
  <address>
    <svg aria-hidden="true"><!-- icon --></svg>
    <span>דיזנגוף 1, תל אביב</span>
  </address>
  
  <!-- Phone -->
  <a
    href="tel:035551234"
    aria-label="התקשר למסעדת השף: 03-5551234"
  >
    📞 03-5551234
  </a>
  
  <!-- Actions -->
  <button aria-label="קבל הוראות ניווט למסעדת השף">
    הוראות ניווט
  </button>
</article>
```

---

### 3.8 Map Accessibility

#### Map Container
```html
<div
  id="map"
  role="application"
  aria-label="מפה אינטראקטיבית של ישראל. השתמש בחצים לניווט"
  tabindex="0"
  onkeydown="handleMapKeyboard(event)"
>
  <!-- Map tiles -->
</div>

<script>
function handleMapKeyboard(e) {
  switch(e.key) {
    case 'ArrowUp':
      map.panBy([0, -50]);
      break;
    case 'ArrowDown':
      map.panBy([0, 50]);
      break;
    case '+':
    case '=':
      map.zoomIn();
      break;
    case '-':
      map.zoomOut();
      break;
  }
}
</script>
```

#### Markers
```html
<!-- POI Marker -->
<button
  class="map-marker"
  aria-label="מסעדת השף, דיזנגוף 1, דירוג 4.5"
  onclick="showPOICard('poi-123')"
>
  🍴
</button>
```

**Alternative:** רשימה נגישה
```html
<!-- For screen readers -->
<div class="sr-only">
  <h2>רשימת מקומות על המפה</h2>
  <ul>
    <li><a href="#poi-123">מסעדת השף - דיזנגוף 1</a></li>
    <li><a href="#poi-456">בית קפה נורדאו - נורדאו 10</a></li>
  </ul>
</div>
```

---

## 4. Screen Reader Testing

### 4.1 כלים

**Windows:**
- NVDA (בחינם)
- JAWS (מסחרי)

**Mac:**
- VoiceOver (מובנה)

**Mobile:**
- iOS VoiceOver
- Android TalkBack

### 4.2 תרחישי בדיקה

#### תרחיש 1: חיפוש מיקום
1. פתח את האתר
2. נווט ל-search bar (Tab)
3. הקלד "מסעדות בתל אביב"
4. שמע suggestions
5. בחר suggestion (Enter)
6. שמע "נמצאו X תוצאות"

#### תרחיש 2: קליק על POI
1. נווט ל-map markers (Tab)
2. שמע "מסעדת השף, דיזנגוף 1, דירוג 4.5"
3. לחץ Enter
4. שמע את כל פרטי הכרטיס
5. נווט לכפתור "הוראות ניווט"
6. סגור עם Esc

---

## 5. Checklist - רשימת בדיקה

### 5.1 תוכן ומבנה
- [ ] כל התמונות עם alt text
- [ ] היררכיית כותרות נכונה (h1-h6)
- [ ] landmarks סמנטיים (nav, main, aside)
- [ ] links מתארים (לא "לחץ כאן")

### 5.2 מקלדת
- [ ] כל הפונקציות נגישות עם Tab
- [ ] Focus indicators ברורים
- [ ] אין מלכודות מקלדת
- [ ] Skip link ל-main content

### 5.3 צבעים וניגודיות
- [ ] Contrast ratio > 4.5:1 (טקסט רגיל)
- [ ] Contrast ratio > 3:1 (טקסט גדול)
- [ ] מידע לא מועבר רק בצבע

### 5.4 טפסים
- [ ] כל השדות עם labels
- [ ] הודעות שגיאה ברורות
- [ ] Required fields מסומנים
- [ ] Autocomplete attributes

### 5.5 תוכן דינמי
- [ ] ARIA live regions
- [ ] Loading states עם aria-busy
- [ ] Modal focus management
- [ ] Error announcements

### 5.6 מובייל
- [ ] Touch targets > 44x44px
- [ ] Pinch-to-zoom מאופשר
- [ ] Orientation support
- [ ] עובד עם screen readers

---

## 6. כלי בדיקה אוטומטיים

### 6.1 Lighthouse (Chrome DevTools)
```bash
# Run audit
lighthouse https://maps.example.co.il --view

# Target: Accessibility score > 95
```

### 6.2 axe DevTools
- Browser extension
- בדיקה אוטומטית של WCAG
- המלצות לתיקון

### 6.3 WAVE (WebAIM)
- Browser extension
- הדגשה ויזואלית של בעיות

### 6.4 Pa11y CI
```bash
# Automated testing in CI
npm install -g pa11y-ci
pa11y-ci --sitemap https://maps.example.co.il/sitemap.xml
```

---

## 7. תיקונים נפוצים

### 7.1 Missing Alt Text
```diff
- <img src="logo.png">
+ <img src="logo.png" alt="מפות ישראל - לוגו">
```

### 7.2 Low Contrast
```diff
- color: #9CA3AF; /* 2.9:1 */
+ color: #4B5563; /* 7.2:1 */
```

### 7.3 No Keyboard Access
```diff
- <div onclick="openMenu()">תפריט</div>
+ <button onclick="openMenu()">תפריט</button>
```

### 7.4 Missing Form Label
```diff
- <input type="text" placeholder="חיפוש">
+ <label for="search">חיפוש</label>
+ <input id="search" type="text">
```

---

## 8. תיעוד נגישות

### 8.1 Accessibility Statement
יש ליצור דף "הצהרת נגישות" עם:
- רמת התאימות (WCAG 2.1 AA)
- תאריך בדיקה אחרונה
- בעיות ידועות
- דרכי יצירת קשר לבעיות נגישות

### 8.2 דוגמה
```
הצהרת נגישות

אתר "מ בעברית" שואף להיות נגיש לכלל האוכלוסייה.

תאימות: WCAG 2.1 רמה AA
תאריך בדיקה: אוקטובר 2026

בעיות ידועות:
- Map navigation עם מקלדת מוגבלת (בעבודה)

יצירת קשר לנגישות:
accessibility@maps.example.co.il
```

---

*מסמך זה מבטיח שהמערכת נגישה לכולם, כנדרש בחוק ובמוסר.*
