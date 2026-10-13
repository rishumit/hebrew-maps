# משימות ואחריות - Task Breakdown

## 1. Phase 0: Setup & Planning

### Infrastructure Team (DevOps)

| # | משימה | אחראי | סטטוס | זמן משוער |
|---|-------|-------|-------|-----------|
| 0.1 | הקמת Git repository + CI/CD | DevOps Lead | ⚪ | 2 days |
| 0.2 | הקמת סביבות (Dev, Staging, Prod) | DevOps | ⚪ | 3 days |
| 0.3 | Setup PostgreSQL + PostGIS | DevOps | ⚪ | 2 days |
| 0.4 | Setup Elasticsearch cluster | DevOps | ⚪ | 2 days |
| 0.5 | Object Storage (S3) setup | DevOps | ⚪ | 1 day |
| 0.6 | Docker Compose לפיתוח מקומי | DevOps | ⚪ | 2 days |

### Product Team

| # | משימה | אחראי | סטטוס | זמן משוער |
|---|-------|-------|-------|-----------|
| 0.7 | אישור סופי של מסמך דרישות | Product Manager | ⚪ | 2 days |
| 0.8 | בחירת stack טכנולוגי | Tech Lead | ⚪ | 3 days |
| 0.9 | UX/UI mockups ראשוניים | Designer | ⚪ | 5 days |

---

## 2. Phase 1: Data Collection

### Data Engineering Team

| # | משימה | אחראי | סטטוס | זמן משוער | תלויות |
|---|-------|-------|-------|-----------|---------|
| 1.1 | הורדת Israel.osm.pbf | Data Engineer | ⚪ | 1 day | - |
| 1.2 | ייבוא OSM ל-PostGIS | Data Engineer | ⚪ | 2 days | 1.1 |
| 1.3 | הורדת מאגר רחובות (data.gov.il) | Data Engineer | ⚪ | 1 day | - |
| 1.4 | הורדת רשימת ישובים | Data Engineer | ⚪ | 1 day | - |
| 1.5 | הורדת מוסדות ציבור (בתי ספר, בריאות) | Data Engineer | ⚪ | 2 days | - |
| 1.6 | Wikidata SPARQL queries | Data Engineer | ⚪ | 2 days | - |
| 1.7 | Wikipedia scraping (top 100 sites) | Data Engineer | ⚪ | 3 days | - |
| 1.8 | עיצוב סכמת DB מאוחדת | Data Engineer + Backend | ⚪ | 3 days | - |
| 1.9 | ETL pipeline - merge OSM + gov.il | Data Engineer | ⚪ | 5 days | 1.2, 1.3 |
| 1.10 | Deduplication (fuzzy matching) | Data Engineer | ⚪ | 3 days | 1.9 |
| 1.11 | העשרה עם Wikipedia data | Data Engineer | ⚪ | 2 days | 1.7, 1.10 |
| 1.12 | Data validation & QA | Data Engineer + QA | ⚪ | 3 days | 1.11 |
| 1.13 | Build search index (Elasticsearch) | Backend Dev | ⚪ | 3 days | 1.12 |
| 1.14 | Hebrew analyzer configuration | Backend Dev | ⚪ | 2 days | 1.13 |

**Milestone 1.0:** ✅ Data Ready (50k+ POI, 1.2k cities, search index)

---

## 3. Phase 2: Backend Development

### Backend Team

| # | משימה | אחראי | סטטוס | זמן משוער | תלויות |
|---|-------|-------|-------|-----------|---------|
| 2.1 | Setup Tiles Service (TileServer GL) | Backend Dev 1 | ⚪ | 3 days | 1.2 |
| 2.2 | Generate tiles cache (zoom 0-16) | Backend Dev 1 | ⚪ | 5 days | 2.1 |
| 2.3 | CDN integration לtiles | DevOps | ⚪ | 2 days | 2.2 |
| 2.4 | POI Service - GET /api/poi/:id | Backend Dev 2 | ⚪ | 2 days | 1.12 |
| 2.5 | POI Service - GET /api/poi (radius) | Backend Dev 2 | ⚪ | 3 days | 2.4 |
| 2.6 | Clustering logic | Backend Dev 2 | ⚪ | 3 days | 2.5 |
| 2.7 | Categories API | Backend Dev 2 | ⚪ | 1 day | - |
| 2.8 | Search Service - Full-text | Backend Dev 3 | ⚪ | 4 days | 1.13 |
| 2.9 | Autocomplete endpoint | Backend Dev 3 | ⚪ | 3 days | 2.8 |
| 2.10 | Fuzzy matching | Backend Dev 3 | ⚪ | 2 days | 2.9 |
| 2.11 | Ranking algorithm | Backend Dev 3 | ⚪ | 3 days | 2.10 |
| 2.12 | Geocoding - Forward | Backend Dev 4 | ⚪ | 3 days | 1.3 |
| 2.13 | Geocoding - Reverse | Backend Dev 4 | ⚪ | 3 days | 2.12 |
| 2.14 | Address parsing (Hebrew) | Backend Dev 4 | ⚪ | 2 days | 2.12 |
| 2.15 | API Gateway setup | DevOps | ⚪ | 2 days | - |
| 2.16 | Rate limiting | Backend Dev | ⚪ | 2 days | 2.15 |
| 2.17 | Caching layer (Redis) | Backend Dev | ⚪ | 2 days | - |
| 2.18 | API documentation (OpenAPI) | Backend Dev | ⚪ | 3 days | All 2.x |
| 2.19 | Load testing | QA Engineer | ⚪ | 3 days | All 2.x |
| 2.20 | Performance optimization | Backend Team | ⚪ | 3 days | 2.19 |

**Milestone 2.0:** ✅ Backend Complete (all APIs working, < 200ms)

---

## 4. Phase 3: Frontend Development

### Frontend Team

| # | משימה | אחראי | סטטוס | זמן משוער | תלויות |
|---|-------|-------|-------|-----------|---------|
| 3.1 | Setup frontend framework | Frontend Lead | ⚪ | 2 days | 0.8 |
| 3.2 | Map component integration | Frontend Dev 1 | ⚪ | 3 days | 2.1 |
| 3.3 | Tiles loading | Frontend Dev 1 | ⚪ | 2 days | 3.2 |
| 3.4 | Pan/Zoom functionality | Frontend Dev 1 | ⚪ | 2 days | 3.3 |
| 3.5 | RTL support | Frontend Dev 1 | ⚪ | 2 days | 3.4 |
| 3.6 | POI markers rendering | Frontend Dev 2 | ⚪ | 3 days | 2.5 |
| 3.7 | Clustering (MarkerCluster) | Frontend Dev 2 | ⚪ | 3 days | 3.6 |
| 3.8 | Custom icons לפי קטגוריה | Designer + Frontend | ⚪ | 2 days | 2.7 |
| 3.9 | Info card component | Frontend Dev 2 | ⚪ | 3 days | - |
| 3.10 | Info card - display data | Frontend Dev 2 | ⚪ | 2 days | 3.9, 2.4 |
| 3.11 | Search bar component | Frontend Dev 3 | ⚪ | 2 days | - |
| 3.12 | Autocomplete integration | Frontend Dev 3 | ⚪ | 3 days | 2.9, 3.11 |
| 3.13 | Search results display | Frontend Dev 3 | ⚪ | 2 days | 2.8 |
| 3.14 | Category filter sidebar | Frontend Dev 4 | ⚪ | 3 days | 2.7 |
| 3.15 | Toggle layers | Frontend Dev 4 | ⚪ | 2 days | 3.14 |
| 3.16 | Geolocation button | Frontend Dev 4 | ⚪ | 2 days | - |
| 3.17 | Responsive design (mobile) | Frontend Team | ⚪ | 5 days | All 3.x |
| 3.18 | Touch gestures | Frontend Dev 1 | ⚪ | 2 days | 3.17 |
| 3.19 | Accessibility (WCAG AA) | Frontend + QA | ⚪ | 4 days | All 3.x |
| 3.20 | Dark mode (optional) | Frontend Dev | ⚪ | 3 days | 3.19 |
| 3.21 | Hebrew fonts & typography | Designer + Frontend | ⚪ | 2 days | - |
| 3.22 | Performance optimization | Frontend Team | ⚪ | 3 days | All 3.x |

**Milestone 3.0:** ✅ Frontend Complete (responsive, accessible, < 2s load)

---

## 5. Phase 4: Integration & Testing

### QA Team

| # | משימה | אחראי | סטטוס | זמן משוער | תלויות |
|---|-------|-------|-------|-----------|---------|
| 4.1 | E2E tests setup (Cypress) | QA Engineer | ⚪ | 2 days | - |
| 4.2 | Critical user flows tests | QA Engineer | ⚪ | 3 days | M3.0 |
| 4.3 | Cross-browser testing | QA Team | ⚪ | 3 days | M3.0 |
| 4.4 | Mobile testing (iOS, Android) | QA Team | ⚪ | 3 days | 3.17 |
| 4.5 | Performance testing (k6) | QA Engineer | ⚪ | 2 days | M2.0 |
| 4.6 | Load test - 1000 concurrent users | QA Engineer | ⚪ | 2 days | 4.5 |
| 4.7 | Beta testing setup | Product Manager | ⚪ | 1 day | - |
| 4.8 | Recruit 20 beta testers | Product Manager | ⚪ | 3 days | 4.7 |
| 4.9 | Beta testing execution | Beta Users + QA | ⚪ | 5 days | 4.8 |
| 4.10 | Collect & prioritize feedback | Product Manager | ⚪ | 2 days | 4.9 |
| 4.11 | Bug fixes - Critical | Dev Team | ⚪ | 5 days | 4.10 |
| 4.12 | Bug fixes - High | Dev Team | ⚪ | 3 days | 4.11 |
| 4.13 | Security audit (OWASP) | Security Engineer | ⚪ | 3 days | M3.0 |
| 4.14 | Penetration testing | Security Team | ⚪ | 3 days | 4.13 |
| 4.15 | Accessibility audit | QA + A11y Expert | ⚪ | 2 days | 3.19 |
| 4.16 | Final regression testing | QA Team | ⚪ | 3 days | All 4.x |

**Milestone 4.0:** ✅ Production Ready (zero critical bugs)

---

## 6. Phase 5: Deployment & Launch

### DevOps + Product Team

| # | משימה | אחראי | סטטוס | זמן משוער | תלויות |
|---|-------|-------|-------|-----------|---------|
| 5.1 | Production environment setup | DevOps Lead | ⚪ | 3 days | M4.0 |
| 5.2 | Deploy backend services | DevOps | ⚪ | 1 day | 5.1 |
| 5.3 | Deploy frontend | DevOps | ⚪ | 1 day | 5.1 |
| 5.4 | CDN configuration | DevOps | ⚪ | 1 day | 5.3 |
| 5.5 | Monitoring setup (Prometheus, Grafana) | DevOps | ⚪ | 2 days | 5.2 |
| 5.6 | Alerting configuration | DevOps | ⚪ | 1 day | 5.5 |
| 5.7 | Logging (ELK stack) | DevOps | ⚪ | 2 days | 5.2 |
| 5.8 | Domain registration | Product Manager | ⚪ | 1 day | - |
| 5.9 | SSL certificates | DevOps | ⚪ | 1 day | 5.8 |
| 5.10 | DNS configuration | DevOps | ⚪ | 1 day | 5.8, 5.9 |
| 5.11 | Status page setup | DevOps | ⚪ | 1 day | - |
| 5.12 | Soft launch (limited users) | Product Manager | ⚪ | 3 days | All 5.x |
| 5.13 | Monitor metrics & fix issues | Dev Team | ⚪ | 3 days | 5.12 |
| 5.14 | Marketing materials | Marketing Team | ⚪ | 5 days | - |
| 5.15 | Public launch announcement | Product Manager | ⚪ | 1 day | 5.13 |
| 5.16 | Post-launch monitoring | DevOps + Dev Team | ⚪ | 7 days | 5.15 |
| 5.17 | Post-mortem & learnings | All Teams | ⚪ | 1 day | 5.16 |

**Milestone 5.0:** 🎉 Launch Complete!

---

## 7. אחריות לפי תפקיד

### Product Manager
- מסמכי דרישות
- אישור features
- Beta testing
- Launch planning
- User feedback

### Tech Lead / Architect
- בחירת טכנולוגיות
- ארכיטקטורה
- Code reviews
- Technical decisions

### Backend Developers (x2-3)
- API development
- Database design
- Performance optimization
- Integration with data sources

### Frontend Developers (x2)
- UI components
- Map integration
- Search & filters
- Responsive design
- Accessibility

### Data Engineer
- Data collection
- ETL pipelines
- Data quality
- Search indexing

### DevOps Engineer
- Infrastructure setup
- CI/CD
- Deployment
- Monitoring
- Security

### QA Engineer
- Test strategy
- Automated testing
- Manual testing
- Bug tracking
- Performance testing

### UX/UI Designer
- Mockups
- Design system
- Icons & assets
- Hebrew typography
- User research

---

## 8. Weekly Sprints

### Sprint Structure (2 weeks)

**Sprint 1-2:** Phase 0 + Phase 1 Start  
**Sprint 3-4:** Phase 1 Complete  
**Sprint 5-7:** Phase 2 (Backend)  
**Sprint 8-10:** Phase 3 (Frontend)  
**Sprint 11-12:** Phase 4 (Testing)  
**Sprint 13:** Phase 5 (Launch)

---

## 9. Daily Standups

**Time:** 10:00 AM  
**Duration:** 15 minutes  
**Format:**
- מה עשיתי אתמול?
- מה אני עושה היום?
- יש לי blockers?

---

## 10. Task Tracking

**Tool:** Jira / GitHub Projects / Linear

**Columns:**
- 📝 Backlog
- 🔜 To Do
- 🚧 In Progress
- 👀 In Review
- ✅ Done

**Labels:**
- `priority: critical`
- `priority: high`
- `priority: medium`
- `priority: low`
- `type: bug`
- `type: feature`
- `type: improvement`
- `phase: 0-5`

---

*מסמך זה מספק breakdown מפורט של כל המשימות. יש לעדכן status בזמן אמת.*
