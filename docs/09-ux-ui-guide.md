# מדריך UX/UI - עיצוב וחווית משתמש

## 1. עקרונות עיצוב

### 1.1 Design Principles

**Hebrew-First**
- עברית היא השפה הראשית, לא תרגום
- RTL (Right-to-Left) מלכתחילה
- גופנים מותאמים לעברית

**Clean & Minimal**
- המפה היא הכוכבת - לא UI
- הסרת עומס מידע
- White space נדיב

**Fast & Responsive**
- תחושת מהירות
- Feedback מיידי על פעולות
- Skeleton screens במקום spinners

**Accessible**
- WCAG AA מינימום
- Keyboard navigation
- Screen reader friendly

---

## 2. Color Palette

### 2.1 Primary Colors

```css
/* Primary */
--primary: #2563EB;        /* כחול עז */
--primary-light: #60A5FA;  /* כחול בהיר */
--primary-dark: #1E40AF;   /* כחול כהה */

/* Secondary */
--secondary: #10B981;      /* ירוק */
--secondary-light: #34D399;
--secondary-dark: #059669;

/* Accent */
--accent: #F59E0B;         /* כתום */
--accent-light: #FBBF24;
--accent-dark: #D97706;
```

### 2.2 Neutral Colors

```css
/* Grays */
--gray-50: #F9FAFB;
--gray-100: #F3F4F6;
--gray-200: #E5E7EB;
--gray-300: #D1D5DB;
--gray-400: #9CA3AF;
--gray-500: #6B7280;
--gray-600: #4B5563;
--gray-700: #374151;
--gray-800: #1F2937;
--gray-900: #111827;

/* Semantic */
--success: #10B981;
--warning: #F59E0B;
--error: #EF4444;
--info: #3B82F6;
```

### 2.3 Category Colors

```css
/* POI Categories */
--category-tourism: #FF6B6B;      /* אדום */
--category-food: #4ECDC4;         /* טורקיז */
--category-health: #95E1D3;       /* ירוק ים */
--category-education: #F38181;    /* ורוד */
--category-transport: #AA96DA;    /* סגול */
--category-shopping: #FCBAD3;     /* ורוד בהיר */
--category-religion: #FFFFD2;     /* צהוב */
--category-nature: #A8D8EA;       /* כחול בהיר */
```

---

## 3. Typography

### 3.1 Font Stack

**עברית:**
```css
font-family: 'Heebo', 'Rubik', 'Assistant', -apple-system, BlinkMacSystemFont, sans-serif;
```

**אנגלית:**
```css
font-family: 'Inter', 'Roboto', -apple-system, BlinkMacSystemFont, sans-serif;
```

### 3.2 Font Sizes

```css
/* Headings */
--text-xs: 0.75rem;    /* 12px */
--text-sm: 0.875rem;   /* 14px */
--text-base: 1rem;     /* 16px */
--text-lg: 1.125rem;   /* 18px */
--text-xl: 1.25rem;    /* 20px */
--text-2xl: 1.5rem;    /* 24px */
--text-3xl: 1.875rem;  /* 30px */
--text-4xl: 2.25rem;   /* 36px */

/* Line Heights */
--leading-tight: 1.25;
--leading-normal: 1.5;
--leading-relaxed: 1.75;
```

### 3.3 Font Weights

```css
--font-light: 300;
--font-normal: 400;
--font-medium: 500;
--font-semibold: 600;
--font-bold: 700;
```

### 3.4 Usage Examples

```css
/* Page Title */
h1 {
  font-size: var(--text-3xl);
  font-weight: var(--font-bold);
  line-height: var(--leading-tight);
  color: var(--gray-900);
}

/* POI Name */
.poi-name {
  font-size: var(--text-lg);
  font-weight: var(--font-semibold);
  color: var(--gray-900);
}

/* Body Text */
body {
  font-size: var(--text-base);
  font-weight: var(--font-normal);
  line-height: var(--leading-normal);
  color: var(--gray-700);
}
```

---

## 4. Layout & Spacing

### 4.1 Spacing Scale

```css
--space-1: 0.25rem;   /* 4px */
--space-2: 0.5rem;    /* 8px */
--space-3: 0.75rem;   /* 12px */
--space-4: 1rem;      /* 16px */
--space-5: 1.25rem;   /* 20px */
--space-6: 1.5rem;    /* 24px */
--space-8: 2rem;      /* 32px */
--space-10: 2.5rem;   /* 40px */
--space-12: 3rem;     /* 48px */
--space-16: 4rem;     /* 64px */
```

### 4.2 Border Radius

```css
--radius-sm: 0.25rem;   /* 4px */
--radius-md: 0.375rem;  /* 6px */
--radius-lg: 0.5rem;    /* 8px */
--radius-xl: 0.75rem;   /* 12px */
--radius-2xl: 1rem;     /* 16px */
--radius-full: 9999px;  /* Circle */
```

### 4.3 Shadows

```css
--shadow-sm: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
--shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
--shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
--shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
--shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
```

---

## 5. Components

### 5.1 Buttons

**Primary Button**
```css
.btn-primary {
  background: var(--primary);
  color: white;
  padding: var(--space-3) var(--space-6);
  border-radius: var(--radius-lg);
  font-weight: var(--font-semibold);
  transition: all 0.2s;
}

.btn-primary:hover {
  background: var(--primary-dark);
  box-shadow: var(--shadow-md);
}
```

**Secondary Button**
```css
.btn-secondary {
  background: white;
  color: var(--primary);
  border: 2px solid var(--primary);
  padding: var(--space-3) var(--space-6);
  border-radius: var(--radius-lg);
}
```

**Icon Button**
```css
.btn-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  background: white;
  box-shadow: var(--shadow);
}
```

---

### 5.2 Search Bar

```css
.search-bar {
  width: 100%;
  max-width: 600px;
  position: relative;
}

.search-input {
  width: 100%;
  padding: var(--space-4) var(--space-12) var(--space-4) var(--space-4);
  border: 2px solid var(--gray-200);
  border-radius: var(--radius-xl);
  font-size: var(--text-base);
  transition: border-color 0.2s;
}

.search-input:focus {
  outline: none;
  border-color: var(--primary);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.search-icon {
  position: absolute;
  right: var(--space-4); /* RTL */
  top: 50%;
  transform: translateY(-50%);
  color: var(--gray-400);
}
```

**Autocomplete Dropdown**
```css
.autocomplete {
  position: absolute;
  top: 100%;
  width: 100%;
  margin-top: var(--space-2);
  background: white;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-xl);
  max-height: 400px;
  overflow-y: auto;
  z-index: 50;
}

.autocomplete-item {
  padding: var(--space-3) var(--space-4);
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.autocomplete-item:hover {
  background: var(--gray-50);
}

.autocomplete-item.selected {
  background: var(--primary-light);
  color: white;
}
```

---

### 5.3 POI Info Card

```css
.poi-card {
  background: white;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  padding: var(--space-6);
  max-width: 400px;
  direction: rtl;
}

.poi-card-header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  margin-bottom: var(--space-4);
}

.poi-card-image {
  width: 100%;
  height: 200px;
  object-fit: cover;
  border-radius: var(--radius-lg);
  margin-bottom: var(--space-4);
}

.poi-name {
  font-size: var(--text-xl);
  font-weight: var(--font-bold);
  color: var(--gray-900);
  margin-bottom: var(--space-2);
}

.poi-category {
  display: inline-block;
  background: var(--primary-light);
  color: white;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-full);
  font-size: var(--text-sm);
}

.poi-rating {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--accent);
  font-weight: var(--font-semibold);
}

.poi-address {
  display: flex;
  align-items: start;
  gap: var(--space-2);
  color: var(--gray-600);
  margin: var(--space-3) 0;
}

.poi-actions {
  display: flex;
  gap: var(--space-3);
  margin-top: var(--space-4);
}
```

---

### 5.4 Category Filter

```css
.filter-sidebar {
  width: 280px;
  background: white;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow);
  padding: var(--space-6);
  direction: rtl;
}

.filter-title {
  font-size: var(--text-lg);
  font-weight: var(--font-semibold);
  margin-bottom: var(--space-4);
}

.filter-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background 0.2s;
}

.filter-item:hover {
  background: var(--gray-50);
}

.filter-item.active {
  background: var(--primary);
  color: white;
}

.filter-icon {
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
}
```

---

### 5.5 Map Controls

```css
.map-controls {
  position: absolute;
  left: var(--space-4); /* RTL - controls on left */
  top: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  z-index: 10;
}

.map-control-btn {
  width: 44px;
  height: 44px;
  background: white;
  border: none;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.map-control-btn:hover {
  box-shadow: var(--shadow-md);
  transform: scale(1.05);
}

.map-control-btn:active {
  transform: scale(0.95);
}
```

**Zoom Controls**
```html
<div class="zoom-controls">
  <button class="map-control-btn" aria-label="הגדל">
    <svg><!-- + icon --></svg>
  </button>
  <button class="map-control-btn" aria-label="הקטן">
    <svg><!-- - icon --></svg>
  </button>
</div>
```

**Geolocation Button**
```css
.geolocation-btn {
  background: var(--primary);
  color: white;
}

.geolocation-btn.active {
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
```

---

## 6. Responsive Breakpoints

```css
/* Mobile First */
--screen-sm: 640px;   /* Small devices */
--screen-md: 768px;   /* Tablets */
--screen-lg: 1024px;  /* Laptops */
--screen-xl: 1280px;  /* Desktops */
--screen-2xl: 1536px; /* Large screens */
```

### 6.1 Mobile (< 768px)

- מפה full-screen
- חיפוש למעלה
- Category filter כ-bottom sheet
- Info card כ-modal מלא
- Hamburger menu

### 6.2 Tablet (768px - 1024px)

- Sidebar ל-filters
- Search bar למעלה
- Info card כ-popup
- 2 columns layout

### 6.3 Desktop (> 1024px)

- Full sidebar
- Search bar למעלה במרכז
- Info card כ-popup גדול
- 3 columns layout

---

## 7. Animations & Transitions

### 7.1 Timing Functions

```css
--ease-in: cubic-bezier(0.4, 0, 1, 1);
--ease-out: cubic-bezier(0, 0, 0.2, 1);
--ease-in-out: cubic-bezier(0.4, 0, 0.2, 1);
```

### 7.2 Common Animations

**Fade In**
```css
@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.fade-in {
  animation: fadeIn 0.3s var(--ease-out);
}
```

**Slide In (RTL)**
```css
@keyframes slideInRight {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(0);
  }
}
```

**Marker Bounce**
```css
@keyframes bounce {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-10px); }
}

.marker-new {
  animation: bounce 0.5s ease-in-out 2;
}
```

---

## 8. Icons

**Icon Set:** Heroicons / Font Awesome / Material Icons

**Sizes:**
```css
--icon-xs: 16px;
--icon-sm: 20px;
--icon-md: 24px;
--icon-lg: 32px;
--icon-xl: 40px;
```

**Common Icons:**
- 🔍 Search: `magnifying-glass`
- 📍 Location: `map-pin`
- ⭐ Rating: `star`
- 🏠 Home: `home`
- 🍴 Restaurant: `utensils`
- 🏥 Hospital: `hospital`
- 🎓 Education: `academic-cap`
- 🚗 Transport: `car`

---

## 9. Dark Mode (Optional)

```css
@media (prefers-color-scheme: dark) {
  :root {
    --bg: var(--gray-900);
    --text: var(--gray-100);
    --border: var(--gray-700);
  }
  
  .map-control-btn {
    background: var(--gray-800);
    color: var(--gray-100);
  }
}
```

---

## 10. Accessibility

### 10.1 Focus States

```css
*:focus-visible {
  outline: 3px solid var(--primary);
  outline-offset: 2px;
}

button:focus-visible {
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.3);
}
```

### 10.2 Contrast Ratios

- Normal text: 4.5:1 minimum
- Large text (18px+): 3:1 minimum
- UI components: 3:1 minimum

### 10.3 Screen Reader Only

```css
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border-width: 0;
}
```

---

*מסמך זה מספק את כל העקרונות והרכיבים לבניית ממשק משתמש מקצועי ונגיש.*
