# תוכנית פרויקט - מערכת מפות ישראל

## סקירה
מסמך זה מפרק את הפרויקט ל**שלבים**, **משימות**, ו**אבני דרך** (milestones).

---

## 1. שלבי הפרויקט - High Level

```
Phase 0: Setup & Planning          [2 שבועות]
Phase 1: Data Collection           [4 שבועות]
Phase 2: Backend Development       [6 שבועות]
Phase 3: Frontend Development      [6 שבועות]
Phase 4: Integration & Testing     [3 שבועות]
Phase 5: Deployment & Launch       [2 שבועות]
────────────────────────────────────────────────
Total: ~23 שבועות (~5-6 חודשים)
```

---

## Phase 0: Setup & Planning (2 שבועות)

### מטרה
הקמת תשתית הפרויקט והבנה מעמיקה של דרישות.

### משימות

#### Week 1: Requirements & Architecture
- [ ] **סיום והסכמה על מסמך דרישות**
  - אישור קטגוריות POI
  - החלטה על שאלות פתוחות (ענן, רישוי, מוניטיזציה)
  
- [ ] **בחירת stack טכנולוגי**
  - Backend framework
  - Frontend framework
  - Database (PostgreSQL+PostGIS מומלץ)
  - Search engine
  - Map rendering library
  
- [ ] **הקמת repository**
  - Git structure
  - Branching strategy
  - CI/CD pipeline (GitHub Actions / GitLab CI)

#### Week 2: Infrastructure Setup
- [ ] **הקמת סביבות**
  - Development environment
  - Staging environment
  - Production infrastructure (cloud/on-prem)
  
- [ ] **הקמת databases**
  - PostgreSQL + PostGIS
  - Search index (Elasticsearch/Meilisearch)
  - Object storage (S3-compatible)
  
- [ ] **הגדרת development workflow**
  - Docker Compose לפיתוח מקומי
  - Environment variables
  - Secrets management

### Milestone 0: ✅ Infrastructure Ready
- כל המפתחים יכולים להריץ את הפרויקט locally
- CI/CD pipeline עובד
- Databases מוכנים

---

## Phase 1: Data Collection (4 שבועות)

### מטרה
איסוף, ניקוי, והטמעה של נתונים גיאוגרפיים.

### Week 1-2: Raw Data Acquisition

#### Task 1.1: OSM Data Download & Import
- [ ] הורד Israel.osm.pbf מ-GeoFabrik
- [ ] ייבוא ל-PostGIS באמצעות osm2pgsql
- [ ] בדיקת תקינות: POI, roads, buildings
- [ ] **Expected output:** טבלאות planet_osm_* מאוכלסות

#### Task 1.2: Government Data
- [ ] הורד מאגר רחובות לאומי (data.gov.il)
- [ ] הורד רשימת ישובים
- [ ] הורד מוסדות ציבור (בתי ספר, מרפאות, בתי חולים)
- [ ] Parse CSV → PostgreSQL
- [ ] **Expected output:** טבלאות streets, cities, institutions

#### Task 1.3: Wikidata & Wikipedia
- [ ] SPARQL query למוזיאונים, אתרי תיירות
- [ ] Scrape תיאורים מויקיפדיה עבור top 100 אתרים
- [ ] הורד תמונות מ-Wikimedia Commons
- [ ] **Expected output:** טבלת enrichment_data

### Week 3: Data Normalization & Enrichment

#### Task 1.4: Schema Design
- [ ] עיצוב סכמת POI מאוחדת
- [ ] עיצוב טבלאות categories, tags
- [ ] Migration scripts
- [ ] **Deliverable:** `schema.sql`

#### Task 1.5: Data Merging
- [ ] Join OSM + data.gov.il לפי כתובת
- [ ] Deduplication (fuzzy matching)
- [ ] העשרה עם Wikipedia data
- [ ] תרגום אוטומטי של שמות חסרים
- [ ] **Expected output:** טבלת `poi_master` מאוחדת

### Week 4: Quality Assurance & Indexing

#### Task 1.6: Data Validation
- [ ] בדוק קואורדינטות בתוך גבולות ישראל
- [ ] validate required fields (name, category, lat/lon)
- [ ] תקן encoding issues
- [ ] manual QA על sample של 100 POI
- [ ] **Target:** 95%+ data quality

#### Task 1.7: Search Indexing
- [ ] בניית search index (Elasticsearch)
- [ ] Hebrew analyzer configuration
- [ ] Index POI names, addresses, descriptions
- [ ] Test autocomplete queries
- [ ] **Expected output:** Search index מוכן

### Milestone 1: ✅ Data Ready
- 50,000+ POI ב-database
- 1,200 ישובים
- Search index עובד
- Data quality report מוכן

---

## Phase 2: Backend Development (6 שבועות)

### מטרה
פיתוח API services ותשתית backend.

### Week 1-2: Core Services

#### Task 2.1: Tiles Service
- [ ] Setup tile server (TileServer GL / Tilemill)
- [ ] Style configuration (OSM style)
- [ ] Generate tiles cache (zoom 0-16)
- [ ] API endpoint: `GET /tiles/{z}/{x}/{y}`
- [ ] CDN integration
- [ ] **Test:** טעינת מפה בדפדפן

#### Task 2.2: POI Service - Basic CRUD
- [ ] API: `GET /api/poi/{id}`
- [ ] API: `GET /api/poi?lat=X&lon=Y&radius=R`
- [ ] API: `GET /api/categories`
- [ ] Clustering logic
- [ ] **Test:** Postman collection

### Week 3-4: Search & Geocoding

#### Task 2.3: Search Service
- [ ] API: `POST /api/search` (full-text)
- [ ] Autocomplete endpoint
- [ ] Fuzzy matching
- [ ] Ranking algorithm (relevance + distance)
- [ ] Hebrew morphology integration
- [ ] **Test:** חיפוש "מסעדות בתל אביב" מחזיר תוצאות

#### Task 2.4: Geocoding Service
- [ ] Forward geocoding (address → coordinates)
- [ ] Reverse geocoding (coordinates → address)
- [ ] Address parsing (רחוב + מספר + עיר)
- [ ] Confidence scoring
- [ ] **Test:** "דיזנגוף 1 תל אביב" → lat/lon

### Week 5-6: Advanced Features & Optimization

#### Task 2.5: Routing (Optional - שלב 2)
- [ ] Integration עם OSRM / Valhalla
- [ ] API: `GET /api/route?from=X,Y&to=A,B`
- [ ] Turn-by-turn directions

#### Task 2.6: Performance Optimization
- [ ] Database query optimization (indexes)
- [ ] Caching layer (Redis)
- [ ] Rate limiting
- [ ] Load testing (Artillery / k6)
- [ ] **Target:** < 200ms response time

#### Task 2.7: API Documentation
- [ ] OpenAPI / Swagger spec
- [ ] API documentation site
- [ ] Code examples

### Milestone 2: ✅ Backend Complete
- כל ה-APIs עובדים
- Performance targets מושגים
- API documentation פורסם
- Unit tests coverage > 70%

---

## Phase 3: Frontend Development (6 שבועות)

### מטרה
בניית ממשק משתמש אינטראקטיבי מלא בעברית.

### Week 1-2: Core Map UI

#### Task 3.1: Map Component
- [ ] Integration עם map library (Mapbox GL / Leaflet / OpenLayers)
- [ ] טעינת tiles מ-Tiles Service
- [ ] Pan/Zoom functionality
- [ ] RTL support
- [ ] **Test:** מפה מוצגת נכון בעברית

#### Task 3.2: POI Markers
- [ ] Fetch POI מ-API
- [ ] הצגת markers על המפה
- [ ] Clustering (MarkerCluster)
- [ ] Custom icons לפי קטגוריה
- [ ] **Test:** 1000+ markers מוצגים חלק

#### Task 3.3: Info Cards
- [ ] קליק על marker → popup
- [ ] עיצוב כרטיס מידע
- [ ] הצגת תמונות, כתובת, טלפון, שעות
- [ ] **Test:** כל השדות מוצגים נכון

### Week 3-4: Search & Navigation

#### Task 3.4: Search Bar
- [ ] Search input עם autocomplete
- [ ] Integration עם Search API
- [ ] הצגת תוצאות בזמן אמת
- [ ] קליק על תוצאה → zoom למיקום
- [ ] **Test:** חיפוש "מוזיאון" מציג suggestions

#### Task 3.5: Category Filter
- [ ] Sidebar עם קטגוריות
- [ ] סינון POI לפי קטגוריה
- [ ] Toggle layers (restaurants, hotels, etc.)
- [ ] **Test:** סינון עובד, מפה מתעדכנת

#### Task 3.6: Geolocation
- [ ] כפתור "מיקום נוכחי"
- [ ] Browser geolocation API
- [ ] Zoom למיקום משתמש
- [ ] **Test:** עובד במובייל ובדסקטופ

### Week 5-6: UX & Polish

#### Task 3.7: Responsive Design
- [ ] Mobile-first design
- [ ] Breakpoints (320px, 768px, 1024px)
- [ ] Touch gestures
- [ ] **Test:** עובד על iPhone, Android, Desktop

#### Task 3.8: Accessibility (WCAG AA)
- [ ] Keyboard navigation
- [ ] Screen reader support
- [ ] Color contrast
- [ ] Focus indicators
- [ ] **Test:** Lighthouse accessibility score > 90

#### Task 3.9: Dark Mode (Optional)
- [ ] Dark theme toggle
- [ ] Dark map tiles
- [ ] Color palette adjustment

#### Task 3.10: Hebrew Fonts & Typography
- [ ] בחירת גופן עברי קריא
- [ ] Line-height ו-spacing
- [ ] RTL text alignment
- [ ] **Test:** טקסט ארוך קריא

### Milestone 3: ✅ Frontend Complete
- ממשק משתמש מלא ועובד
- Responsive (מובייל + דסקטופ)
- WCAG AA compliant
- User testing עבר בהצלחה

---

## Phase 4: Integration & Testing (3 שבועות)

### Week 1: Integration Testing

#### Task 4.1: End-to-End Tests
- [ ] Cypress / Playwright setup
- [ ] תרחיש: טעינת מפה
- [ ] תרחיש: חיפוש מיקום
- [ ] תרחיש: קליק על POI
- [ ] **Target:** כל התרחישים הקריטיים מכוסים

#### Task 4.2: Performance Testing
- [ ] Load test עם 1000 concurrent users
- [ ] Stress test
- [ ] Latency measurement
- [ ] **Target:** < 2s load time, < 200ms API response

### Week 2: User Acceptance Testing (UAT)

#### Task 4.3: Beta Testing
- [ ] גיוס 20 beta testers
- [ ] איסוף feedback
- [ ] Bug tracking
- [ ] Prioritization

#### Task 4.4: Bug Fixes
- [ ] תיקון critical bugs
- [ ] תיקון UX issues
- [ ] **Target:** Zero critical bugs

### Week 3: Security & Compliance

#### Task 4.5: Security Audit
- [ ] OWASP Top 10 check
- [ ] SQL injection tests
- [ ] XSS tests
- [ ] Rate limiting verification
- [ ] **Deliverable:** Security report

#### Task 4.6: Accessibility Audit
- [ ] Screen reader testing
- [ ] Keyboard navigation testing
- [ ] Lighthouse audit
- [ ] **Target:** WCAG AA compliant

### Milestone 4: ✅ Production Ready
- כל הבאגים הקריטיים תוקנו
- Performance requirements מושגים
- Security audit עבר
- UAT הושלם

---

## Phase 5: Deployment & Launch (2 שבועות)

### Week 1: Deployment

#### Task 5.1: Production Deployment
- [ ] Deploy backend services
- [ ] Deploy frontend
- [ ] Configure CDN
- [ ] Setup monitoring (Prometheus, Grafana)
- [ ] Setup logging (ELK stack)
- [ ] **Test:** Production environment stable

#### Task 5.2: DNS & SSL
- [ ] Domain registration
- [ ] SSL certificates
- [ ] CDN configuration
- [ ] **Deliverable:** https://maps.example.co.il live

### Week 2: Launch

#### Task 5.3: Soft Launch
- [ ] Announce to limited audience
- [ ] Monitor metrics
- [ ] Quick bug fixes
- [ ] **Duration:** 3-5 days

#### Task 5.4: Public Launch
- [ ] Marketing announcement
- [ ] Social media
- [ ] Press release (אם רלוונטי)
- [ ] Monitor traffic spike
- [ ] **Date:** [TBD]

#### Task 5.5: Post-Launch Support
- [ ] On-call rotation
- [ ] Bug hotfixes
- [ ] Performance monitoring
- [ ] User feedback collection

### Milestone 5: ✅ Launch Complete
- מערכת live בפרודקשן
- משתמשים פעילים
- Monitoring עובד
- Support process במקום

---

## 2. Dependencies & Critical Path

```
Phase 0 (Setup)
    ↓
Phase 1 (Data) ──────┐
    ↓                │
Phase 2 (Backend)    │
    ↓                │
Phase 3 (Frontend) ←─┘
    ↓
Phase 4 (Testing)
    ↓
Phase 5 (Launch)
```

**Critical path:**
- Phase 1 must complete לפני Phase 2
- Phase 2 must complete לפני Phase 3
- Phases 2 & 3 יכולים לעבוד במקביל (אחרי Phase 1)

---

## 3. Team Structure (מומלץ)

| תפקיד | אחריות | Phases |
|-------|--------|--------|
| **Project Manager** | תיאום, lוחות זמנים | 0-5 |
| **Backend Developer (x2)** | API, database, tiles | 1-2 |
| **Frontend Developer (x2)** | UI, UX, integration | 3 |
| **Data Engineer** | Data collection, ETL | 1 |
| **DevOps Engineer** | Infra, CI/CD, deploy | 0, 2, 5 |
| **QA Engineer** | Testing, automation | 4 |
| **UX/UI Designer** | Mockups, design system | 0, 3 |

**Total:** 8-10 people

---

## 4. Risks & Mitigation

| Risk | Impact | Mitigation |
|------|--------|-----------|
| OSM data quality נמוכה | High | העשרה מ-data.gov.il + manual QA |
| Search performance גרוע | Medium | Optimize indexes, caching |
| Mobile performance איטי | High | Lazy loading, tile caching |
| Hebrew rendering issues | Medium | בדיקות מוקדמות, Hebrew fonts |
| Deployment delays | Medium | Dry-run deployments, staging |

---

## 5. Success Metrics (KPIs)

### Launch Day
- [ ] Zero critical bugs
- [ ] < 2 sec load time
- [ ] 100+ concurrent users without issues

### Month 1
- [ ] 10,000+ unique visitors
- [ ] 50,000+ searches
- [ ] 95%+ uptime
- [ ] Avg session > 3 min

### Month 3
- [ ] 50,000+ unique visitors
- [ ] User feedback > 4/5 stars
- [ ] POI coverage > 60,000
- [ ] Mobile users > 60%

---

*תוכנית זו היא living document. יש לעדכן לפי progress בפועל וכשינויים בדרישות.*
