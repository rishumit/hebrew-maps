# אסטרטגיית בדיקות ו-QA

## 1. פירמידת בדיקות

```
              ┌─────────────┐
              │   Manual    │  ← 5%
              │   Testing   │
              └─────────────┘
          ┌───────────────────┐
          │   E2E Tests       │  ← 10%
          │  (Cypress/PW)     │
          └───────────────────┘
      ┌───────────────────────────┐
      │  Integration Tests        │  ← 25%
      │  (API, DB, Services)      │
      └───────────────────────────┘
  ┌───────────────────────────────────┐
  │      Unit Tests                   │  ← 60%
  │   (Functions, Components)         │
  └───────────────────────────────────┘
```

---

## 2. Unit Tests

### 2.1 Backend Unit Tests

**Coverage Target:** 80%+

#### דוגמה - Geocoding Function
```javascript
// geocoding.service.test.js
describe('Geocoding Service', () => {
  
  test('parseAddress should extract street and number', () => {
    const result = parseAddress('דיזנגוף 1 תל אביב');
    expect(result).toEqual({
      street: 'דיזנגוף',
      number: '1',
      city: 'תל אביב'
    });
  });
  
  test('should handle address without number', () => {
    const result = parseAddress('רחוב הרצל תל אביב');
    expect(result.number).toBeNull();
  });
  
  test('should handle Hebrew and English street names', () => {
    const result1 = parseAddress('Dizengoff 1');
    const result2 = parseAddress('דיזנגוף 1');
    expect(result1.street).toBe('Dizengoff');
    expect(result2.street).toBe('דיזנגוף');
  });
});
```

#### דוגמה - POI Distance Calculation
```javascript
describe('POI Service - Distance', () => {
  
  test('calculateDistance should return meters', () => {
    const point1 = { lat: 32.0853, lon: 34.7818 };
    const point2 = { lat: 32.0861, lon: 34.7792 };
    const distance = calculateDistance(point1, point2);
    expect(distance).toBeCloseTo(285, 0); // ~285 meters
  });
  
  test('findPOIWithinRadius should filter by distance', async () => {
    const center = { lat: 32.0853, lon: 34.7818 };
    const pois = await findPOIWithinRadius(center, 500);
    
    pois.forEach(poi => {
      const dist = calculateDistance(center, poi.location);
      expect(dist).toBeLessThanOrEqual(500);
    });
  });
});
```

---

### 2.2 Frontend Unit Tests

**Framework:** Jest + React Testing Library

#### דוגמה - Search Component
```javascript
// SearchBar.test.jsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import SearchBar from './SearchBar';

describe('SearchBar Component', () => {
  
  test('renders search input', () => {
    render(<SearchBar />);
    expect(screen.getByPlaceholderText(/חיפוש/i)).toBeInTheDocument();
  });
  
  test('shows autocomplete suggestions on input', async () => {
    const mockFetch = jest.fn().mockResolvedValue({
      suggestions: [
        { text: 'מסעדת השף', type: 'poi' },
        { text: 'מסעדות בתל אביב', type: 'query' }
      ]
    });
    
    render(<SearchBar onSearch={mockFetch} />);
    const input = screen.getByRole('textbox');
    
    fireEvent.change(input, { target: { value: 'מסעד' } });
    
    await waitFor(() => {
      expect(screen.getByText('מסעדת השף')).toBeInTheDocument();
    });
  });
  
  test('calls onSearch when suggestion clicked', async () => {
    const onSearch = jest.fn();
    render(<SearchBar onSearch={onSearch} />);
    
    // ... trigger autocomplete
    fireEvent.click(screen.getByText('מסעדת השף'));
    
    expect(onSearch).toHaveBeenCalledWith(expect.objectContaining({
      text: 'מסעדת השף'
    }));
  });
});
```

---

## 3. Integration Tests

### 3.1 API Integration Tests

**Framework:** Supertest (Node.js) / pytest (Python)

#### דוגמה - POI API
```javascript
// poi.api.test.js
const request = require('supertest');
const app = require('../app');

describe('POI API Integration', () => {
  
  test('GET /api/poi/:id returns POI data', async () => {
    const response = await request(app)
      .get('/api/poi/poi-12345')
      .expect(200);
    
    expect(response.body).toMatchObject({
      id: 'poi-12345',
      name_he: expect.any(String),
      location: {
        lat: expect.any(Number),
        lon: expect.any(Number)
      }
    });
  });
  
  test('GET /api/poi with radius returns nearby POIs', async () => {
    const response = await request(app)
      .get('/api/poi')
      .query({ lat: 32.0853, lon: 34.7818, radius: 1000 })
      .expect(200);
    
    expect(response.body.results).toBeInstanceOf(Array);
    response.body.results.forEach(poi => {
      expect(poi.distance).toBeLessThanOrEqual(1000);
    });
  });
  
  test('returns 404 for non-existent POI', async () => {
    await request(app)
      .get('/api/poi/invalid-id')
      .expect(404);
  });
});
```

---

### 3.2 Database Integration Tests

```javascript
describe('Database POI Queries', () => {
  beforeAll(async () => {
    await db.connect();
    await db.seed(); // Insert test data
  });
  
  afterAll(async () => {
    await db.cleanup();
    await db.disconnect();
  });
  
  test('spatial query returns POI within bounds', async () => {
    const pois = await db.query(`
      SELECT * FROM poi
      WHERE ST_DWithin(
        location::geography,
        ST_MakePoint(34.7818, 32.0853)::geography,
        1000
      )
    `);
    
    expect(pois.length).toBeGreaterThan(0);
  });
});
```

---

## 4. End-to-End Tests

**Framework:** Cypress / Playwright

### 4.1 Critical User Flows

#### תרחיש 1: טעינת מפה וחיפוש
```javascript
// e2e/map-search.spec.js
describe('Map and Search Flow', () => {
  
  it('loads map and searches for location', () => {
    cy.visit('/');
    
    // מפה נטענת
    cy.get('.map-container').should('be.visible');
    cy.get('.leaflet-tile').should('be.visible');
    
    // חיפוש
    cy.get('input[placeholder*="חיפוש"]').type('מסעדות בתל אביב');
    
    // המתנה לאוטומטית-השלמה
    cy.get('.autocomplete-suggestions').should('be.visible');
    cy.contains('מסעדות בתל אביב').click();
    
    // בדיקת תוצאות
    cy.get('.poi-marker').should('have.length.greaterThan', 0);
    cy.url().should('include', 'q=מסעדות');
  });
});
```

#### תרחיש 2: קליק על POI
```javascript
it('clicks POI marker and shows info card', () => {
  cy.visit('/?lat=32.0853&lon=34.7818&zoom=15');
  
  // המתן לטעינת markers
  cy.get('.poi-marker').first().click();
  
  // כרטיס מידע
  cy.get('.poi-info-card').should('be.visible');
  cy.get('.poi-name').should('not.be.empty');
  cy.get('.poi-address').should('contain', 'תל אביב');
  cy.get('.poi-rating').should('be.visible');
});
```

#### תרחיש 3: Responsive - Mobile
```javascript
it('works on mobile viewport', () => {
  cy.viewport('iphone-x');
  cy.visit('/');
  
  // מפה responsive
  cy.get('.map-container').should('be.visible');
  
  // תפריט המבורגר
  cy.get('.menu-toggle').click();
  cy.get('.mobile-menu').should('be.visible');
  
  // חיפוש mobile
  cy.get('.search-button').click();
  cy.get('input[type="search"]').should('be.focused');
});
```

---

## 5. Performance Testing

### 5.1 Load Testing

**Tool:** k6 / Artillery

#### דוגמה - Search API Load Test
```javascript
// load-test/search.js
import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  stages: [
    { duration: '2m', target: 100 },  // ramp up
    { duration: '5m', target: 100 },  // stay at 100 users
    { duration: '2m', target: 200 },  // spike
    { duration: '5m', target: 200 },
    { duration: '2m', target: 0 },    // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500'], // 95% < 500ms
    http_req_failed: ['rate<0.01'],   // error rate < 1%
  },
};

export default function () {
  const res = http.get('https://api.maps.example/api/search/autocomplete?q=מסעדה');
  
  check(res, {
    'status is 200': (r) => r.status === 200,
    'response time < 200ms': (r) => r.timings.duration < 200,
  });
  
  sleep(1);
}
```

**Expected Results:**
- 95% requests < 500ms
- Error rate < 1%
- Throughput: 1000+ req/sec

---

### 5.2 Frontend Performance

**Tool:** Lighthouse CI

**Targets:**
- Performance: > 90
- Accessibility: > 95
- Best Practices: > 90
- SEO: > 90

**Key Metrics:**
- First Contentful Paint: < 1.5s
- Largest Contentful Paint: < 2.5s
- Time to Interactive: < 3.5s
- Cumulative Layout Shift: < 0.1

---

## 6. Manual QA Checklist

### 6.1 Functional Testing

#### Map Display
- [ ] מפה נטענת בכל הדפדפנים (Chrome, Firefox, Safari, Edge)
- [ ] Zoom in/out עובד חלק
- [ ] Pan (גרירה) עובד
- [ ] Tiles נטענים מהר (< 2s)
- [ ] RTL נכון (כפתורים בצד ימין)

#### Search
- [ ] חיפוש מחזיר תוצאות רלוונטיות
- [ ] Autocomplete עובד (< 200ms)
- [ ] תיקון שגיאות כתיב
- [ ] חיפוש בעברית ובאנגלית
- [ ] חיפוש עם ניקוד (דִּיזֶנְגוֹף)
- [ ] חיפוש עם טעויות (דיזנגו → דיזנגוף)

#### POI
- [ ] Markers מוצגים נכון
- [ ] Clustering עובד (זום נמוך)
- [ ] קליק על marker → info card
- [ ] כל השדות מוצגים (שם, כתובת, טלפון...)
- [ ] תמונות נטענות
- [ ] קישור לאתר חיצוני עובד

#### Filters
- [ ] סינון לפי קטגוריה
- [ ] Toggle multiple categories
- [ ] סינון לפי rating
- [ ] איפוס filters

---

### 6.2 Cross-Browser Testing

| Feature | Chrome | Firefox | Safari | Edge | Mobile Safari | Mobile Chrome |
|---------|--------|---------|--------|------|---------------|---------------|
| Map Display | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Search | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ |
| Geolocation | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ |
| Touch Gestures | N/A | N/A | N/A | N/A | ✅ | ✅ |

**Legend:**
- ✅ Works perfectly
- ⚠️ Minor issues
- ❌ Broken
- N/A Not applicable

---

### 6.3 Accessibility Testing

#### Keyboard Navigation
- [ ] Tab מעביר בין כפתורים
- [ ] Enter על marker פותח info card
- [ ] Esc סוגר modals
- [ ] Focus indicators ברורים

#### Screen Reader
- [ ] Alt text לכל התמונות
- [ ] ARIA labels on buttons
- [ ] כותרות (h1, h2) נכונות
- [ ] Error messages מוקראים

#### Visual
- [ ] Contrast ratio > 4.5:1 (WCAG AA)
- [ ] גופן קריא (min 14px)
- [ ] Zoom 200% - עדיין קריא

---

## 7. Data Quality Tests

### 7.1 POI Data Validation

**Automated Checks:**
```sql
-- בדיקה: כל POI עם קואורדינטות תקינות
SELECT COUNT(*) FROM poi
WHERE lat IS NULL OR lon IS NULL;
-- Expected: 0

-- בדיקה: קואורדינטות בתוך ישראל
SELECT COUNT(*) FROM poi
WHERE lat < 29.5 OR lat > 33.3
   OR lon < 34.2 OR lon > 35.9;
-- Expected: < 1%

-- בדיקה: כל POI עם שם בעברית
SELECT COUNT(*) FROM poi
WHERE name_he IS NULL OR name_he = '';
-- Expected: 0

-- בדיקה: duplicates
SELECT name_he, lat, lon, COUNT(*)
FROM poi
GROUP BY name_he, lat, lon
HAVING COUNT(*) > 1;
-- Expected: 0
```

---

### 7.2 Manual Sampling

**Sample Size:** 100 POI randomly selected

**Checklist per POI:**
- [ ] קואורדינטות נכונות (Google Maps comparison)
- [ ] שם נכון
- [ ] כתובת מדויקת
- [ ] טלפון תקין (אם קיים)
- [ ] Website עובד (אם קיים)
- [ ] תמונות רלוונטיות
- [ ] קטגוריה נכונה

**Pass Criteria:** 95%+ accuracy

---

## 8. Security Testing

### 8.1 OWASP Top 10

- [ ] **SQL Injection** - Parameterized queries
- [ ] **XSS** - Input sanitization
- [ ] **CSRF** - Tokens on state-changing requests
- [ ] **Broken Authentication** - Password policies
- [ ] **Sensitive Data Exposure** - HTTPS only
- [ ] **XML External Entities** - N/A (no XML)
- [ ] **Broken Access Control** - Role checks
- [ ] **Security Misconfiguration** - Headers, CORS
- [ ] **Using Components with Known Vulnerabilities** - npm audit
- [ ] **Insufficient Logging** - Audit trail

---

### 8.2 Penetration Testing

**Tools:**
- OWASP ZAP
- Burp Suite
- SQLMap

**Test Cases:**
- API injection attempts
- Rate limiting bypass
- Authorization bypass
- Input fuzzing

---

## 9. Regression Testing

**Automation:** CI/CD Pipeline

**Trigger:** Every commit to `main`

**Steps:**
1. Run unit tests
2. Run integration tests
3. Run E2E tests (smoke)
4. Security scan
5. Performance regression check

**Pass Criteria:** All tests green

---

## 10. Bug Severity Levels

| Level | Definition | SLA |
|-------|------------|-----|
| **Critical** | System down, data loss | Fix immediately |
| **High** | Major feature broken | Fix within 24h |
| **Medium** | Minor feature issue | Fix within 1 week |
| **Low** | Cosmetic, enhancement | Backlog |

---

*מערכת בדיקות זו מבטיחה איכות גבוהה בכל שכבות המערכת.*
