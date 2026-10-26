# ארכיטקטורה - מערכת מפות ישראל

## תיאור כללי
מסמך זה מתאר את הארכיטקטורה המערכתית **ברמה לוגית** בלבד. אין כאן בחירת טכנולוגיות, אלא הגדרת גבולות אחריות ותקשורת בין רכיבים.

---

## 1. ארכיטקטורת המערכת - High Level

```
┌─────────────────────────────────────────────────────┐
│                  Client Devices                     │
│           (Desktop Browser, Mobile Web)             │
└─────────────────┬───────────────────────────────────┘
                  │ HTTPS
                  ↓
┌─────────────────────────────────────────────────────┐
│              API Gateway / Load Balancer            │
│         (Rate limiting, Authentication)             │
└────┬──────────┬─────────────┬──────────────────┬───┘
     │          │             │                  │
     ↓          ↓             ↓                  ↓
┌─────────┐ ┌──────────┐ ┌─────────────┐ ┌────────────┐
│  Tiles  │ │  Search  │ │  POI/Data   │ │ Geocoding  │
│ Service │ │  Service │ │   Service   │ │  Service   │
└────┬────┘ └────┬─────┘ └──────┬──────┘ └─────┬──────┘
     │           │              │              │
     └───────────┴──────────────┴──────────────┘
                          ↓
          ┌───────────────────────────────┐
          │       Data Layer              │
          │  ┌─────────────────────────┐  │
          │  │  Spatial Database       │  │
          │  │  (PostGIS/Spatial DB)   │  │
          │  └─────────────────────────┘  │
          │  ┌─────────────────────────┐  │
          │  │  Search Index           │  │
          │  │  (Elasticsearch/Solr)   │  │
          │  └─────────────────────────┘  │
          │  ┌─────────────────────────┐  │
          │  │  Object Storage         │  │
          │  │  (Images, Tiles Cache)  │  │
          │  └─────────────────────────┘  │
          └───────────────────────────────┘
```

---

## 2. רכיבי המערכת - תפקידים ואחריות

### 2.1 Frontend (Client)
**תפקיד:**  
- הצגת מפה אינטראקטיבית
- ממשק משתמש בעברית עם RTL מלא
- ניהול state של המפה (zoom, center, layers)
- חיפוש ואוטומטית-השלמה
- הצגת POI markers וכרטיסי מידע

**תקשורת:**
- מושך map tiles מ-Tiles Service
- שולח שאילתות חיפוש ל-Search Service
- מבקש POI data מ-POI Service
- Geocoding requests ל-Geocoding Service

**אחריות מרכזית:**
- Responsive design (מובייל + דסקטופ)
- State management (מיקום משתמש, שכבות פעילות)
- Performance (lazy loading, caching)

---

### 2.2 Tiles Service
**תפקיד:**  
הגשת אריחי מפה (tiles) בפורמט רסטר או וקטור לפי רמת זום ואזור גיאוגרפי.

**Input:**
- `GET /tiles/{z}/{x}/{y}.png` או `.pbf` (vector tiles)

**Output:**
- קובץ PNG (raster) או Protobuf (vector)

**אחריות:**
- Pre-generation או on-the-fly rendering
- Caching (CDN / Object Storage)
- תמיכה בשכבות שונות (road, satellite, terrain)

**מקור נתונים:**
- OpenStreetMap processed tiles
- Custom style rendering

---

### 2.3 Search Service
**תפקיד:**  
חיפוש טקסטואלי של כתובות, ערים, POI, ושמות מקומות.

**Input:**
```json
{
  "query": "מסעדות בתל אביב",
  "lat": 32.0853,
  "lon": 34.7818,
  "radius": 5000,
  "limit": 10
}
```

**Output:**
```json
{
  "results": [
    {
      "id": "poi-12345",
      "name": "מסעדת דוגמה",
      "category": "restaurant",
      "lat": 32.0853,
      "lon": 34.7818,
      "distance": 450,
      "score": 0.95
    }
  ]
}
```

**אחריות:**
- Autocomplete (real-time suggestions)
- Fuzzy matching (תיקון שגיאות כתיב)
- Ranking לפי relevance + distance
- ניתוח מורפולוגי בעברית

**טכנולוגיה מומלצת (לא נדרש לבחור עכשיו):**
- Full-text search engine (Elasticsearch, Solr, Meilisearch)
- Hebrew morphological analyzer

---

### 2.4 POI Service
**תפקיד:**  
ניהול והגשת נקודות עניין (Points of Interest).

**API Examples:**

**קבלת POI ספציפי:**
```
GET /api/poi/{id}
```

**חיפוש POI באזור:**
```
GET /api/poi?lat=32.08&lon=34.78&radius=1000&category=tourism
```

**קבלת קטגוריות:**
```
GET /api/categories
```

**Output:**
```json
{
  "id": "poi-12345",
  "name_he": "מגדל דוד",
  "name_en": "Tower of David",
  "category": "tourism",
  "sub_category": "museum",
  "lat": 31.7767,
  "lon": 35.2281,
  "address": "רחוב דוד 1, ירושלים",
  "phone": "02-6265333",
  "website": "https://example.com",
  "opening_hours": {
    "sunday": "09:00-17:00",
    "monday": "09:00-17:00",
    "friday": "closed"
  },
  "images": ["url1", "url2"],
  "rating": 4.7,
  "tags": ["מוזיאון", "היסטוריה", "תיירות"]
}
```

**אחריות:**
- CRUD operations על POI
- Clustering (קיבוץ markers בזום נמוך)
- Filtering לפי קטגוריה / tags
- Integration עם מקורות נתונים חיצוניים

---

### 2.5 Geocoding Service
**תפקיד:**  
המרה בין כתובות לקואורדינטות ולהיפך.

**Forward Geocoding (Address → Coordinates):**
```
GET /api/geocode?address=דיזנגוף 1 תל אביב

Response:
{
  "lat": 32.0853,
  "lon": 34.7818,
  "address": "דיזנגוף 1, תל אביב-יפו",
  "confidence": 0.95
}
```

**Reverse Geocoding (Coordinates → Address):**
```
GET /api/reverse?lat=32.0853&lon=34.7818

Response:
{
  "address": "דיזנגוף 1, תל אביב-יפו",
  "city": "תל אביב",
  "street": "דיזנגוף",
  "house_number": "1"
}
```

**אחריות:**
- תמיכה בכתובות בעברית ובאנגלית
- טיפול ב-abbreviations (רח' = רחוב)
- Confidence scoring

---

## 3. Data Layer - שכבת הנתונים

### 3.1 Spatial Database
**תפקיד:**  
אחסון נתונים גיאוגרפיים וביצוע שאילתות מרחביות.

**מה נשמר:**
- POI records עם נקודות גיאוגרפיות
- רחובות וישובים (polygons / linestrings)
- גבולות ערים ואזורים
- כבישים ונתיבים

**שאילתות נפוצות:**
- "מצא כל POI ברדיוס X ממיקום Y"
- "האם נקודה Z נמצאת בתוך גבולות עיר A"
- "חשב מסלול קצר ביותר בין שתי נקודות"

**דרישות:**
- PostGIS extensions או Spatial DB אחר
- Indexing (GiST / R-Tree)
- תמיכה ב-GeoJSON

---

### 3.2 Search Index
**תפקיד:**  
אינדקס מיטוב לחיפוש טקסטואלי מהיר.

**מה נשמר:**
- שמות POI בעברית ואנגלית
- כתובות מלאות
- תגיות וקטגוריות
- Synonyms (מילים נרדפות)

**דרישות:**
- Hebrew analyzer (ניתוח מורפולוגי)
- Fuzzy matching
- Real-time indexing

---

### 3.3 Object Storage
**תפקיד:**  
אחסון קבצים גדולים: תמונות, tiles cache, assets.

**מה נשמר:**
- תמונות של POI
- Pre-rendered map tiles (cache)
- Static assets (icons, fonts)

**דרישות:**
- CDN integration
- Public read access
- Versioning (אופציונלי)

---

## 4. זרימת נתונים - תרחישי שימוש

### תרחיש 1: טעינת מפה ראשונית
```
1. User → Frontend: load map at Tel Aviv
2. Frontend → Tiles Service: GET /tiles/{z}/{x}/{y}
3. Tiles Service → Object Storage: fetch cached tile
4. Frontend: render tiles on map
5. Frontend → POI Service: get POI in viewport
6. POI Service → Spatial DB: spatial query
7. Frontend: display markers
```

### תרחיש 2: חיפוש "מסעדות בירושלים"
```
1. User → Frontend: types "מסעדות בירו..."
2. Frontend → Search Service: autocomplete query
3. Search Service → Search Index: fuzzy match
4. Frontend: display suggestions
5. User → selects result
6. Frontend → POI Service: get full POI data
7. Frontend: zoom to location + show info card
```

### תרחיש 3: קליק על POI marker
```
1. User → clicks marker
2. Frontend → POI Service: GET /api/poi/{id}
3. POI Service → Spatial DB: fetch full record
4. POI Service → Object Storage: get images URLs
5. Frontend: render info card with data
```

---

## 5. דרישות לא-פונקציונליות

### 5.1 ביצועים
- Tiles: < 100ms per tile
- Search autocomplete: < 200ms
- POI data: < 500ms
- Concurrent users: 1,000+

### 5.2 סקלביליות
- Horizontal scaling לכל service
- Load balancing
- Database read replicas

### 5.3 זמינות
- 99.5% uptime
- Graceful degradation (אם search down, מפה עדיין עובדת)
- Health checks

### 5.4 אבטחה
- HTTPS בלבד
- Rate limiting
- Input validation
- CORS configuration
- No sensitive data in logs

---

## 6. עקרונות עיצוב

### 6.1 Separation of Concerns
כל service אחראי רק על תחום אחד:
- Tiles ≠ POI data
- Search ≠ Geocoding

### 6.2 Stateless Services
כל request עצמאי, אין session state בשרת.

### 6.3 API-First
כל communication דרך REST/GraphQL APIs מוגדרים מראש.

### 6.4 Caching Strategy
- Client-side: tiles, POI data
- Server-side: search results, geocoding
- CDN: static assets

---

## 7. תרשים ER - מבני נתונים מרכזיים

```
┌──────────────┐         ┌──────────────┐
│     POI      │         │   Category   │
├──────────────┤         ├──────────────┤
│ id (PK)      │────┐    │ id (PK)      │
│ name_he      │    │    │ name_he      │
│ name_en      │    └───→│ name_en      │
│ category_id  │ (FK)    │ icon         │
│ lat          │         │ color        │
│ lon          │         └──────────────┘
│ address      │
│ city_id (FK) │    ┌──────────────┐
│ phone        │    │    City      │
│ website      │    ├──────────────┤
│ rating       │    │ id (PK)      │
└──────────────┘    │ name_he      │
                    │ name_en      │
      ↓             │ boundary     │ (polygon)
┌──────────────┐    │ population   │
│    Image     │    └──────────────┘
├──────────────┤
│ id (PK)      │
│ poi_id (FK)  │
│ url          │
│ caption      │
└──────────────┘
```

---

*מסמך זה מגדיר **ארכיטקטורה לוגית** בלבד. בחירת טכנולוגיות ספציפיות (React/Vue, Node/Python, PostgreSQL/MongoDB) תיעשה בשלב הטכני.*
