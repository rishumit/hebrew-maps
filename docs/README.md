# 📚 תיעוד פרויקט - מערכת מ בעברית

ברוכים הבאים לתיעוד המלא של פרויקט המפות ישראל.

---

## 🗂️ מבנה המסמכים

### מסמכים קיימים

1. **[01-requirements.md](./01-requirements.md)** ⭐ **התחל כאן**
   - מה המערכת צריכה לעשות
   - קטגוריות POI
   - מקורות נתונים מומלצים
   - דרישות ביצועים ונגישות

2. **[02-architecture.md](./02-architecture.md)**
   - ארכיטקטורה לוגית של המערכת
   - שכבות ורכיבים
   - זרימת נתונים
   - API specifications

3. **[03-data-collection.md](./03-data-collection.md)** ⭐ **חשוב מאוד**
   - איך לאסוף נתונים מ-OpenStreetMap
   - שימוש ב-data.gov.il
   - Wikidata & Wikipedia
   - סקריפטים לדוגמה

4. **[07-project-plan.md](./07-project-plan.md)** ⭐ **תוכנית ביצוע**
   - פירוק לשלבים (5 phases)
   - משימות מפורטות
   - לוחות זמנים
   - אבני דרך (milestones)

---

### מסמכים שיש ליצור (לפי הצורך)

5. **04-technical-spec.md** (טכני מפורט)
   - API endpoints specification
   - Database schema
   - Data models
   - Error handling

6. **05-quality-testing.md** (בדיקות)
   - Test strategy
   - Unit / Integration / E2E tests
   - QA checklist
   - Performance benchmarks

7. **06-deployment-ops.md** (תפעול)
   - Deployment pipeline
   - Monitoring & alerting
   - Backup & disaster recovery
   - Scaling strategy

8. **08-tasks-responsibilities.md** (ניהול)
   - רשימת משימות (task list)
   - אחריות לכל משימה
   - Status tracking

9. **09-ux-ui-guide.md** (עיצוב)
   - Design system
   - Color palette
   - Typography (גופנים עבריים)
   - Component library

10. **10-accessibility.md** (נגישות)
    - WCAG 2.1 AA requirements
    - Screen reader support
    - Keyboard navigation
    - Testing checklist

---

## 🚀 איך להתחיל?

### למנהל פרויקט / Product Manager:
1. קרא `01-requirements.md` להבנת המוצר
2. קרא `07-project-plan.md` לתכנון ביצוע
3. התאם את התוכנית לצוות שלך

### למפתח Backend:
1. קרא `02-architecture.md` להבנת המערכת
2. קרא `03-data-collection.md` לאיסוף נתונים
3. התחל מ-Phase 1 ב-`07-project-plan.md`

### למפתח Frontend:
1. קרא `01-requirements.md` (חלק UX)
2. קרא `02-architecture.md` (API interfaces)
3. המתן ל-Backend APIs או עבוד עם mock data

### ל-Data Engineer:
1. קרא `03-data-collection.md` ⭐
2. התחל להוריד OSM + data.gov.il
3. בנה ETL pipeline

---

## 📊 מצב הפרויקט

| שלב | סטטוס | הערות |
|-----|-------|-------|
| **Phase 0: Planning** | ✅ הושלם | מסמכי דרישות + החלטות ([adr/](./adr)) |
| **Phase 1: Data** | ✅ הושלם | צינור ETL: ‏OSM + data.gov.il + Wikidata, דוח איכות |
| **Phase 2: Backend** | ✅ הושלם | API, חיפוש עברי, גאוקודינג, אריחים, ניווט |
| **Phase 3: Frontend** | ✅ הושלם | ממשק RTL, מובייל/PWA, נגישות |
| **Phase 4: Testing** | ✅ הושלם | יחידה, אינטגרציה, E2E, ביצועים, CI |
| **Phase 5: Launch** | 🟡 מוכן להתקנה | הרצת `setup.cmd` במחשב היעד |

**מסמכי המימוש:** [11-implementation.md](./11-implementation.md) · [12-operations.md](./12-operations.md) · [api.md](./api.md) · [adr/](./adr)

---

## 🔑 נקודות מפתח

### עקרונות הפרויקט
1. **איכות על פני מהירות** - לא משגרים עד שזה מושלם
2. **נתונים פתוחים תחילה** - OSM + data.gov.il לפני APIs מסחריים
3. **עברית ברמה ראשונה** - לא תרגום של אנגלית
4. **Mobile-first** - רוב המשתמשים במובייל
5. **נגישות ללא פשרות** - WCAG AA מינימום

### שאלות פתוחות (לקבל החלטה)
- [ ] Stack טכנולוגי (React/Vue? Node/Python?)
- [ ] ענן או On-prem?
- [ ] מודל עסקי (בחינם? מנויים? B2B?)
- [ ] ניווט GPS בשלב 1 או 2?

---

## 📞 יצירת קשר

לשאלות או הבהרות על התיעוד:
- צור Issue ב-GitHub
- פנה למנהל הפרויקט
- עדכן מסמכים זה ישירות (Pull Request)

---

**עדכון אחרון:** אוקטובר 2026  
**גרסת תיעוד:** 1.0
