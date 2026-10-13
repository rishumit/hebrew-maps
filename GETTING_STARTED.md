# מדריך התחלה - מפות בעברית

> **עדכון (אוקטובר 2026):** המערכת מומשה. להתקנה: לחיצה כפולה על `setup.cmd`. פרטים ב-[README.md](./README.md),
> ובדוח המימוש: [docs/11-implementation.md](./docs/11-implementation.md). המסמך שלהלן הוא מדריך התכנון המקורי.

## 🎯 מה יש לך כאן?

מערכת תיעוד **מקצועית ומלאה** לבניית תוכנת מפות בעברית ברמת Google Maps.

---

## 📚 המסמכים - לפי סדר קריאה

### שלב 1: הבנה והכנה (חובה לכולם)

1. **[README.md](./README.md)** - סקירה ומפת דרכים
2. **[docs/01-requirements.md](./docs/01-requirements.md)** ⭐ - **מה צריך לבנות**
   - קטגוריות POI
   - מקורות נתונים
   - דרישות ביצועים

3. **[docs/03-data-collection.md](./docs/03-data-collection.md)** ⭐⭐ - **איך לאסוף נתונים**
   - OpenStreetMap מדריך מפורט
   - data.gov.il
   - Wikidata + Wikipedia
   - סקריפטים לדוגמה

### שלב 2: ארכיטקטורה וטכנולוגיה

4. **[docs/02-architecture.md](./docs/02-architecture.md)** - מבנה המערכת
   - שכבות ורכיבים
   - API specifications
   - Data flow

5. **[docs/04-technical-spec.md](./docs/04-technical-spec.md)** - מפרט טכני
   - API endpoints מפורטים
   - Database schema
   - Data models
   - Error handling

### שלב 3: תכנון ביצוע

6. **[docs/07-project-plan.md](./docs/07-project-plan.md)** ⭐ - **תוכנית 6 חודשים**
   - 5 Phases
   - משימות מפורטות
   - Milestones
   - לוחות זמנים

7. **[docs/08-tasks-responsibilities.md](./docs/08-tasks-responsibilities.md)** - פירוק למשימות
   - Task breakdown מלא
   - אחריות לפי תפקיד
   - Dependencies
   - Sprint planning

### שלב 4: איכות ועיצוב

8. **[docs/05-quality-testing.md](./docs/05-quality-testing.md)** - בדיקות
   - Unit, Integration, E2E
   - Performance testing
   - Security testing
   - QA checklist

9. **[docs/09-ux-ui-guide.md](./docs/09-ux-ui-guide.md)** - עיצוב UI/UX
   - Color palette
   - Typography עברית
   - Components
   - Responsive design

10. **[docs/10-accessibility.md](./docs/10-accessibility.md)** - נגישות
    - WCAG 2.1 AA
    - Screen readers
    - Keyboard navigation
    - Testing checklist

### שלב 5: תפעול

11. **[docs/06-deployment-ops.md](./docs/06-deployment-ops.md)** - פריסה ותפעול
    - CI/CD pipeline
    - Monitoring & alerting
    - Disaster recovery
    - Cost optimization

---

## 🚀 איך להתחיל?

### אם אתה Product Manager / מנהל פרויקט:
```
1. קרא docs/01-requirements.md
2. קרא docs/07-project-plan.md
3. קרא docs/08-tasks-responsibilities.md
4. התאם תוכנית לצוות שלך
```

### אם אתה Backend Developer:
```
1. קרא docs/02-architecture.md
2. קרא docs/03-data-collection.md ⭐ (מאוד חשוב!)
3. קרא docs/04-technical-spec.md
4. התחל מ-Phase 1 ב-docs/07-project-plan.md
```

### אם אתה Frontend Developer:
```
1. קרא docs/01-requirements.md (חלק UX)
2. קרא docs/09-ux-ui-guide.md
3. קרא docs/10-accessibility.md
4. המתן ל-Backend APIs או עבוד עם mock data
```

### אם אתה Data Engineer:
```
1. קרא docs/03-data-collection.md ⭐⭐⭐ (זה הכל בשבילך!)
2. התחל להוריד Israel.osm.pbf
3. התקן osm2pgsql + PostgreSQL + PostGIS
4. בנה ETL pipeline
```

### אם אתה DevOps Engineer:
```
1. קרא docs/06-deployment-ops.md
2. קרא docs/02-architecture.md (Infrastructure)
3. הקם סביבות (Dev, Staging, Prod)
4. Setup CI/CD pipeline
```

### אם אתה QA Engineer:
```
1. קרא docs/05-quality-testing.md
2. קרא docs/10-accessibility.md
3. Setup testing frameworks
4. כתוב test plans
```

---

## 🔑 נקודות מפתח

### מה המערכת עושה?
- **מפה אינטראקטיבית** של ישראל (כמו Google Maps)
- **חיפוש** ערים, רחובות, מקומות
- **50,000+ POI** (Points of Interest)
- **קטגוריות:** תיירות, מזון, בריאות, חינוך...
- **עברית ברמה ראשונה** - RTL, גופנים, UI

### מאיפה הנתונים?
1. **OpenStreetMap** - בסיס המפה + רוב ה-POI (חינם!)
2. **data.gov.il** - רחובות רשמיים, מוסדות ציבור
3. **Wikidata + Wikipedia** - תיאורים, תמונות
4. **רשות עתיקות** - אתרי עתיקות
5. **רשות הטבע** - גנים לאומיים

### כמה זמן לוקח?
- **Phase 0 (Setup):** 2 שבועות
- **Phase 1 (Data):** 4 שבועות
- **Phase 2 (Backend):** 6 שבועות
- **Phase 3 (Frontend):** 6 שבועות
- **Phase 4 (Testing):** 3 שבועות
- **Phase 5 (Launch):** 2 שבועות
- **סה"כ:** ~5-6 חודשים

### כמה אנשים?
- Backend Developers: 2-3
- Frontend Developers: 2
- Data Engineer: 1
- DevOps: 1
- QA: 1
- Designer: 1
- Product Manager: 1
- **סה"כ:** 8-10 איש

---

## 📊 סטטוס פרויקט

| Phase | משימות | סטטוס |
|-------|--------|-------|
| **0. Setup** | Infrastructure, Planning | ✅ |
| **1. Data** | OSM, gov.il, ETL | ✅ |
| **2. Backend** | APIs, Database, Search | ✅ |
| **3. Frontend** | UI, Map, Search | ✅ |
| **4. Testing** | QA, Security, A11y | ✅ |
| **5. Launch** | Deploy, Monitor, Go Live | 🟡 מוכן – מריצים `setup.cmd` |

---

## ✅ Checklist לפני שמתחילים

### החלטות שצריך לקבל:
- [ ] Stack טכנולוגי (React? Vue? Node? Python?)
- [ ] ענן (AWS? Azure? GCP?) או On-prem?
- [ ] Database (PostgreSQL מומלץ מאוד!)
- [ ] Search engine (Elasticsearch? Meilisearch?)
- [ ] Map library (Mapbox? Leaflet? OpenLayers?)
- [ ] מודל עסקי (חינם? SaaS? B2B?)
- [ ] תקציב חודשי

### כלים להתקנה:
```bash
# Backend
- PostgreSQL 14+ + PostGIS
- Node.js 18+ / Python 3.10+
- Elasticsearch 8+
- Redis

# Frontend
- Node.js 18+
- Package manager (npm/yarn/pnpm)

# Data
- osm2pgsql
- osmium-tool
- wget / curl

# DevOps
- Docker + Docker Compose
- Git
- Cloud CLI (aws/az/gcloud)
```

---

## 📞 שאלות נפוצות

### האם צריך לדעת GIS?
**לא הכרחי**, אבל עוזר. המסמכים מסבירים הכל.

### האם OSM מספיק טוב?
**כן!** OSM של ישראל איכותי מאוד. מכיל רוב המידע שצריך.

### כמה עולה להריץ את זה?
**תלוי בגודל:**
- Development: ~$0 (local)
- Small production: ~$200-500/חודש
- Medium scale (10k users): ~$1000-2000/חודש

### האם צריך רישיון מיוחד?
**לא.** OSM + data.gov.il רישיונות פתוחים לגמרי.

### מה עם Google Maps API?
**לא צריך!** כל הפרויקט בנוי על נתונים פתוחים.

---

## 🎓 למידה נוספת

### GIS & Maps
- [LearnOSM](https://learnosm.org/he/) - למד OSM בעברית
- [PostGIS Documentation](https://postgis.net/docs/)
- [Mapbox GL JS Docs](https://docs.mapbox.com/mapbox-gl-js/)

### APIs & Data
- [OpenStreetMap Wiki](https://wiki.openstreetmap.org/)
- [Overpass API Tutorial](https://wiki.openstreetmap.org/wiki/Overpass_API)
- [data.gov.il](https://data.gov.il)

---

## 🤝 תרומה

מצאת שגיאה? יש הצעה לשיפור?
- פתח Issue
- שלח Pull Request
- עדכן את המסמכים

---

## 📄 רישיון

המסמכים: [לקבוע]  
הקוד (עתידי): [לקבוע]

---

**בהצלחה! 🚀**

*מסמכים אלה נוצרו באוקטובר 2026 לצורך פרויקט מפות בעברית ברמה מקצועית.*
