# מפרט טכני - API ומבני נתונים

## 1. API Endpoints - מפורט

### 1.1 Tiles Service

#### GET /tiles/{z}/{x}/{y}.{format}
הגשת אריח מפה בזום ופוזיציה מסוימת.

**Parameters:**
- `z` (path): zoom level (0-18)
- `x` (path): tile X coordinate
- `y` (path): tile Y coordinate
- `format` (path): `png`, `jpg`, `pbf` (vector)

**Response:**
- `200 OK` - Binary tile data
- `404 Not Found` - Tile doesn't exist
- `304 Not Modified` - If-Modified-Since matched

**Headers:**
```
Content-Type: image/png
Cache-Control: public, max-age=86400
ETag: "abc123"
```

---

### 1.2 POI Service

#### GET /api/poi/{id}
קבלת POI ספציפי לפי ID.

**Response 200:**
```json
{
  "id": "poi-12345",
  "name_he": "מגדל דוד",
  "name_en": "Tower of David",
  "category": "tourism",
  "sub_category": "museum",
  "location": {
    "lat": 31.7767,
    "lon": 35.2281
  },
  "address": {
    "street": "דרך יפו",
    "house_number": "1",
    "city": "ירושלים",
    "postal_code": "9410119"
  },
  "contact": {
    "phone": "02-6265333",
    "email": "info@tod.org.il",
    "website": "https://www.tod.org.il"
  },
  "opening_hours": {
    "sunday": "09:00-17:00",
    "monday": "09:00-17:00",
    "tuesday": "09:00-17:00",
    "wednesday": "09:00-17:00",
    "thursday": "09:00-17:00",
    "friday": "09:00-14:00",
    "saturday": "closed"
  },
  "media": {
    "images": [
      "https://cdn.maps.example/poi-12345/1.jpg",
      "https://cdn.maps.example/poi-12345/2.jpg"
    ],
    "thumbnail": "https://cdn.maps.example/poi-12345/thumb.jpg"
  },
  "metadata": {
    "rating": 4.7,
    "review_count": 1250,
    "wheelchair_accessible": true,
    "tags": ["מוזיאון", "היסטוריה", "ירושלים", "תיירות"],
    "price_level": 2,
    "last_updated": "2026-10-01T10:30:00Z"
  },
  "description_he": "מגדל דוד הוא מוזיאון המציג את תולדות ירושלים...",
  "description_en": "The Tower of David is a museum..."
}
```

**Errors:**
- `404` - POI not found
- `500` - Internal server error

---

#### GET /api/poi
חיפוש POI לפי קריטריונים.

**Query Parameters:**
- `lat` (required): latitude
- `lon` (required): longitude
- `radius` (optional): radius in meters (default: 1000, max: 50000)
- `category` (optional): filter by category
- `tags` (optional): comma-separated tags
- `limit` (optional): max results (default: 50, max: 200)
- `offset` (optional): pagination offset

**Example Request:**
```
GET /api/poi?lat=32.0853&lon=34.7818&radius=2000&category=restaurant&limit=20
```

**Response 200:**
```json
{
  "results": [
    {
      "id": "poi-67890",
      "name_he": "מסעדת השף",
      "name_en": "Chef's Restaurant",
      "category": "restaurant",
      "location": {
        "lat": 32.0861,
        "lon": 34.7792
      },
      "distance": 285,
      "rating": 4.5,
      "thumbnail": "https://cdn.maps.example/poi-67890/thumb.jpg"
    }
  ],
  "meta": {
    "total": 45,
    "returned": 20,
    "offset": 0,
    "query_time_ms": 23
  }
}
```

---

#### GET /api/categories
קבלת רשימת קטגוריות זמינות.

**Response 200:**
```json
{
  "categories": [
    {
      "id": "tourism",
      "name_he": "תיירות",
      "name_en": "Tourism",
      "icon": "map-marker-alt",
      "color": "#FF6B6B",
      "sub_categories": [
        {
          "id": "museum",
          "name_he": "מוזיאון",
          "name_en": "Museum"
        },
        {
          "id": "viewpoint",
          "name_he": "תצפית",
          "name_en": "Viewpoint"
        }
      ]
    },
    {
      "id": "food",
      "name_he": "מזון",
      "name_en": "Food",
      "icon": "utensils",
      "color": "#4ECDC4",
      "sub_categories": [
        {
          "id": "restaurant",
          "name_he": "מסעדה",
          "name_en": "Restaurant"
        },
        {
          "id": "cafe",
          "name_he": "בית קפה",
          "name_en": "Cafe"
        }
      ]
    }
  ]
}
```

---

### 1.3 Search Service

#### POST /api/search
חיפוש full-text.

**Request Body:**
```json
{
  "query": "מסעדות בתל אביב",
  "location": {
    "lat": 32.0853,
    "lon": 34.7818
  },
  "filters": {
    "category": "restaurant",
    "tags": ["כשר", "טבעוני"],
    "rating_min": 4.0
  },
  "limit": 20,
  "offset": 0
}
```

**Response 200:**
```json
{
  "results": [
    {
      "id": "poi-11111",
      "name_he": "מסעדת הבשרים",
      "name_en": "The Meat Restaurant",
      "category": "restaurant",
      "location": {
        "lat": 32.0853,
        "lon": 34.7818
      },
      "distance": 150,
      "score": 0.95,
      "rating": 4.6,
      "match_fields": ["name", "tags"]
    }
  ],
  "meta": {
    "total": 127,
    "returned": 20,
    "query_time_ms": 45
  }
}
```

---

#### GET /api/search/autocomplete
השלמה אוטומטית בזמן הקלדה.

**Query Parameters:**
- `q` (required): search query (min 2 chars)
- `lat` (optional): user latitude for proximity boost
- `lon` (optional): user longitude
- `limit` (optional): max suggestions (default: 10)

**Example:**
```
GET /api/search/autocomplete?q=מסעד&lat=32.08&lon=34.78&limit=10
```

**Response 200:**
```json
{
  "suggestions": [
    {
      "text": "מסעדת השף",
      "type": "poi",
      "id": "poi-12345",
      "category": "restaurant",
      "location": {
        "lat": 32.0861,
        "lon": 34.7792
      }
    },
    {
      "text": "מסעדות בתל אביב",
      "type": "search_query"
    },
    {
      "text": "מסעדה איטלקית",
      "type": "category"
    }
  ],
  "query_time_ms": 12
}
```

---

### 1.4 Geocoding Service

#### GET /api/geocode
Forward geocoding - כתובת לקואורדינטות.

**Query Parameters:**
- `address` (required): full address string
- `city` (optional): city name for disambiguation
- `country` (optional): default "IL"

**Example:**
```
GET /api/geocode?address=דיזנגוף 1 תל אביב
```

**Response 200:**
```json
{
  "results": [
    {
      "formatted_address": "דיזנגוף 1, תל אביב-יפו",
      "location": {
        "lat": 32.0853,
        "lon": 34.7818
      },
      "confidence": 0.98,
      "components": {
        "street": "דיזנגוף",
        "house_number": "1",
        "city": "תל אביב-יפו",
        "postal_code": "6203854"
      },
      "bounds": {
        "northeast": {"lat": 32.0854, "lon": 34.7819},
        "southwest": {"lat": 32.0852, "lon": 34.7817}
      }
    }
  ]
}
```

---

#### GET /api/reverse
Reverse geocoding - קואורדינטות לכתובת.

**Query Parameters:**
- `lat` (required)
- `lon` (required)

**Response 200:**
```json
{
  "formatted_address": "דיזנגוף 1, תל אביב-יפו",
  "components": {
    "street": "דיזנגוף",
    "house_number": "1",
    "neighborhood": "לב העיר",
    "city": "תל אביב-יפו",
    "district": "מחוז תל אביב",
    "postal_code": "6203854",
    "country": "ישראל",
    "country_code": "IL"
  },
  "location": {
    "lat": 32.0853,
    "lon": 34.7818
  }
}
```

---

## 2. Database Schema

### 2.1 POI Table
```sql
CREATE TABLE poi (
    id SERIAL PRIMARY KEY,
    uuid VARCHAR(36) UNIQUE NOT NULL,
    
    -- Names
    name_he VARCHAR(255) NOT NULL,
    name_en VARCHAR(255),
    name_ar VARCHAR(255),
    
    -- Classification
    category_id INT REFERENCES categories(id),
    sub_category_id INT,
    
    -- Location
    location GEOGRAPHY(POINT, 4326) NOT NULL,
    address_street VARCHAR(255),
    address_number VARCHAR(20),
    city_id INT REFERENCES cities(id),
    postal_code VARCHAR(10),
    
    -- Contact
    phone VARCHAR(20),
    email VARCHAR(255),
    website VARCHAR(500),
    
    -- Hours (JSON)
    opening_hours JSONB,
    
    -- Media
    images JSONB,  -- array of URLs
    thumbnail_url VARCHAR(500),
    
    -- Metadata
    description_he TEXT,
    description_en TEXT,
    tags TEXT[],
    rating DECIMAL(2,1),
    review_count INT DEFAULT 0,
    price_level INT,  -- 1-4
    wheelchair_accessible BOOLEAN,
    
    -- Source tracking
    source VARCHAR(50),  -- 'osm', 'wikidata', 'gov_il', 'manual'
    source_id VARCHAR(100),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    -- Indexes
    CHECK (rating >= 0 AND rating <= 5),
    CHECK (price_level >= 1 AND price_level <= 4)
);

-- Spatial index
CREATE INDEX idx_poi_location ON poi USING GIST(location);

-- Search indexes
CREATE INDEX idx_poi_name_he ON poi USING GIN(to_tsvector('hebrew', name_he));
CREATE INDEX idx_poi_tags ON poi USING GIN(tags);
CREATE INDEX idx_poi_category ON poi(category_id);
```

---

### 2.2 Categories Table
```sql
CREATE TABLE categories (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(50) UNIQUE NOT NULL,
    name_he VARCHAR(100) NOT NULL,
    name_en VARCHAR(100) NOT NULL,
    icon VARCHAR(50),
    color VARCHAR(7),  -- hex color
    parent_id INT REFERENCES categories(id),
    sort_order INT DEFAULT 0
);

INSERT INTO categories (slug, name_he, name_en, icon, color) VALUES
('tourism', 'תיירות', 'Tourism', 'map-marker-alt', '#FF6B6B'),
('food', 'מזון', 'Food', 'utensils', '#4ECDC4'),
('health', 'בריאות', 'Health', 'hospital', '#95E1D3'),
('education', 'חינוך', 'Education', 'graduation-cap', '#F38181'),
('transport', 'תחבורה', 'Transport', 'bus', '#AA96DA');
```

---

### 2.3 Cities Table
```sql
CREATE TABLE cities (
    id SERIAL PRIMARY KEY,
    name_he VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    name_ar VARCHAR(100),
    city_code VARCHAR(10) UNIQUE,  -- CBS city code
    boundary GEOGRAPHY(MULTIPOLYGON, 4326),
    population INT,
    district VARCHAR(50),
    center_location GEOGRAPHY(POINT, 4326)
);

CREATE INDEX idx_cities_boundary ON cities USING GIST(boundary);
```

---

### 2.4 Streets Table
```sql
CREATE TABLE streets (
    id SERIAL PRIMARY KEY,
    name_he VARCHAR(255) NOT NULL,
    name_en VARCHAR(255),
    city_id INT REFERENCES cities(id),
    geometry GEOGRAPHY(LINESTRING, 4326),
    street_code VARCHAR(20),
    
    UNIQUE(name_he, city_id)
);

CREATE INDEX idx_streets_geom ON streets USING GIST(geometry);
CREATE INDEX idx_streets_city ON streets(city_id);
```

---

## 3. Data Models (JSON Schemas)

### 3.1 OpeningHours Schema
```json
{
  "type": "object",
  "properties": {
    "sunday": {"type": "string", "pattern": "^\\d{2}:\\d{2}-\\d{2}:\\d{2}$|^closed$"},
    "monday": {"type": "string"},
    "tuesday": {"type": "string"},
    "wednesday": {"type": "string"},
    "thursday": {"type": "string"},
    "friday": {"type": "string"},
    "saturday": {"type": "string"},
    "notes": {"type": "string"}
  }
}
```

**Example:**
```json
{
  "sunday": "09:00-17:00",
  "monday": "09:00-17:00",
  "friday": "09:00-14:00",
  "saturday": "closed",
  "notes": "סגור בחגים"
}
```

---

## 4. Error Responses

### Standard Error Format
```json
{
  "error": {
    "code": "INVALID_PARAMETER",
    "message": "The 'radius' parameter must be between 1 and 50000",
    "details": {
      "parameter": "radius",
      "provided": 100000,
      "max": 50000
    }
  }
}
```

### Error Codes
| Code | HTTP Status | Description |
|------|-------------|-------------|
| `INVALID_PARAMETER` | 400 | Invalid query parameter |
| `MISSING_PARAMETER` | 400 | Required parameter missing |
| `NOT_FOUND` | 404 | Resource doesn't exist |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests |
| `INTERNAL_ERROR` | 500 | Server error |
| `SERVICE_UNAVAILABLE` | 503 | Temporary unavailable |

---

## 5. Rate Limiting

**Default Limits:**
- Tiles: 1000 requests/minute/IP
- Search: 100 requests/minute/IP
- POI: 500 requests/minute/IP
- Geocoding: 60 requests/minute/IP

**Headers:**
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1697889600
```

---

## 6. Authentication (Future)

**API Key (Optional for Premium Features):**
```
Authorization: Bearer YOUR_API_KEY
```

**Response when missing:**
```json
{
  "error": {
    "code": "AUTHENTICATION_REQUIRED",
    "message": "This endpoint requires authentication"
  }
}
```

---

*מסמך זה מגדיר את ה-API Contract המלא. שינויים דורשים versioning (v1, v2).*
