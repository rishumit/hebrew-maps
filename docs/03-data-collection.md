# מדריך איסוף נתונים - מפות בעברית

## מטרה
מסמך זה מפרט **כיצד ומאיפה** לאסוף את הנתונים הגיאוגרפיים והמידע על נקודות עניין בישראל.

---

## 1. סקירת מקורות נתונים

### טבלת מקורות ראשית
| מקור | נתונים | איכות | רישיון | עדיפות |
|------|---------|-------|---------|---------|
| OpenStreetMap | מפות, כבישים, POI | גבוהה | ODbL (פתוח) | **1** |
| data.gov.il | רחובות, ישובים, מוסדות | רשמי | CC0 / ממשלתי | **1** |
| Wikidata | אתרים, מוזיאונים, היסטוריה | טובה | CC0 | **2** |
| Wikipedia | תיאורים, תמונות | טובה | CC-BY-SA | **2** |
| GeoNames | ישובים עולמיים | בינונית | CC-BY | **3** |

---

## 2. OpenStreetMap (OSM) - המקור המרכזי

### 2.1 מה כלול ב-OSM?
- **כבישים ורחובות** - כל רחוב בישראל עם שם בעברית ואנגלית
- **ישובים** - ערים, כפרים, קיבוצים עם גבולות
- **POI** - מסעדות, בתי קפה, בתי כנסת, חנויות, פארקים...
- **טופוגרפיה** - הרים, נחלים, חופים
- **תחבורה ציבורית** - תחנות אוטובוס, רכבת

### 2.2 הורדת נתוני OSM לישראל

#### אופציה 1: קובץ PBF מלא (מומלץ)
```bash
# הורד את כל ישראל (קובץ ~100-200MB)
wget https://download.geofabrik.de/asia/israel-and-palestine-latest.osm.pbf
```

**יתרונות:**
- עדכני (מתעדכן מדי יום)
- כולל הכל
- מהיר להוריד

**חסרונות:**
- גדול (צריך לעבד אותו)

#### אופציה 2: Overpass API (שאילתות ממוקדות)
```bash
# דוגמה: הורד כל המסעדות בתל אביב
curl -X POST "https://overpass-api.de/api/interpreter" \
  -d '[out:json];
      area["name"="תל אביב-יפו"]->.a;
      (
        node["amenity"="restaurant"](area.a);
        way["amenity"="restaurant"](area.a);
      );
      out center;'
```

**יתרונות:**
- ממוקד (רק מה שצריך)
- Real-time

**חסרונות:**
- צריך לכתוב queries מורכבות
- מוגבל בגודל response

### 2.3 עיבוד OSM לדאטהבייס

**כלים:**
- `osm2pgsql` - ייבוא ל-PostgreSQL+PostGIS
- `osmium` - חיתוך וסינון קבצי OSM
- `osmosis` - עיבוד וסנכרון

**דוגמת pipeline:**
```bash
# 1. התקנת כלים
apt-get install osm2pgsql postgresql-postgis

# 2. יצירת DB
createdb israel_maps
psql -d israel_maps -c "CREATE EXTENSION postgis;"

# 3. ייבוא
osm2pgsql -d israel_maps \
  --create --slim \
  --latlong \
  israel-and-palestine-latest.osm.pbf

# 4. שאילתות
psql -d israel_maps -c "
  SELECT name, amenity, ST_X(way) as lon, ST_Y(way) as lat
  FROM planet_osm_point
  WHERE amenity = 'restaurant'
  LIMIT 10;
"
```

---

## 3. data.gov.il - נתוני ממשלה

### 3.1 מאגר הרחובות הלאומי
**URL:** https://data.gov.il/dataset/321

**מה כלול:**
- כל רחוב בישראל
- עיר, סמל עיר, שם רחוב בעברית ואנגלית
- קואורדינטות (לא תמיד מדויקות)

**פורמט:** CSV

**דוגמת שורה:**
```csv
city_code,city_name_he,city_name_en,street_code,street_name_he,street_name_en,lat,lon
5000,תל אביב-יפו,Tel Aviv-Yafo,1,"דיזנגוף","Dizengoff",32.0853,34.7818
```

**שימוש:**
1. הורד CSV
2. Parse לתוך DB
3. העשר עם נתוני OSM (join לפי שם רחוב + עיר)

### 3.2 רשימת ישובים
**URL:** https://data.gov.il/dataset/citiesandsettelments

**מה כלול:**
- כל ישוב בישראל (~1,200)
- סמל ישוב, שם בעברית/אנגלית/ערבית
- נתונים דמוגרפיים

### 3.3 מוסדות ציבור
**דוגמאות:**
- בתי ספר: https://data.gov.il/dataset/schools
- מרפאות קחולים: https://data.gov.il/dataset/health-clinics
- בתי חולים: https://data.gov.il/dataset/hospitals
- גני ילדים: https://data.gov.il/dataset/kindergartens

**אסטרטגיה:**
1. זהה datasets רלוונטיים
2. הורד ל-CSV/JSON
3. Normalize לפורמט POI אחיד
4. ייבוא ל-DB

---

## 4. Wikidata + Wikipedia

### 4.1 Wikidata SPARQL
**URL:** https://query.wikidata.org/

**דוגמת query - כל המוזיאונים בישראל:**
```sparql
SELECT ?item ?itemLabel ?coord ?image WHERE {
  ?item wdt:P31 wd:Q33506;        # instance of museum
        wdt:P17 wd:Q801;          # country = Israel
        wdt:P625 ?coord.          # has coordinates
  OPTIONAL { ?item wdt:P18 ?image. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "he,en". }
}
```

**פורמט תוצאה:** JSON

**יתרון:**
- תמונות בחינם (Wikimedia Commons)
- תיאורים מקושרים לויקיפדיה
- Multilingual

### 4.2 Wikipedia API
**דוגמה - קבלת תיאור של "מגדל דוד":**
```bash
curl "https://he.wikipedia.org/api/rest_v1/page/summary/מגדל_דוד"
```

**Response:**
```json
{
  "title": "מגדל דוד",
  "extract": "מגדל דוד הוא מוזיאון המציג את תולדות ירושלים...",
  "thumbnail": {
    "source": "https://upload.wikimedia.org/..."
  },
  "coordinates": {
    "lat": 31.7767,
    "lon": 35.2281
  }
}
```

---

## 5. תהליך איסוף מלא - Pipeline מומלץ

```
┌─────────────────────────────────────────────────┐
│              שלב 1: הורדת נתונים גולמיים        │
├─────────────────────────────────────────────────┤
│ • הורד Israel.osm.pbf מ-GeoFabrik              │
│ • הורד מאגר רחובות מ-data.gov.il               │
│ • הורד רשימת ישובים                            │
│ • Export Wikidata למוזיאונים ואתרים            │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│           שלב 2: Parsing & Normalization        │
├─────────────────────────────────────────────────┤
│ • OSM → PostgreSQL+PostGIS (osm2pgsql)          │
│ • CSV → Pandas → DB                             │
│ • JSON → Structured tables                      │
│ • נרמול שמות (דיזנגוף = Dizengoff)              │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│            שלב 3: Data Enrichment                │
├─────────────────────────────────────────────────┤
│ • Join OSM + data.gov.il (לפי כתובת)           │
│ • העשר עם תיאורים מוויקיפדיה                   │
│ • הוסף תמונות מ-Wikimedia Commons               │
│ • חשב קטגוריות מאוחדות                          │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│          שלב 4: Quality Assurance                │
├─────────────────────────────────────────────────┤
│ • בדוק קואורדינטות תקינות (בתוך ישראל)         │
│ • הסר duplicates                                │
│ • תקן encoding issues (Hebrew)                  │
│ • Validate required fields                      │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│             שלב 5: Indexing                      │
├─────────────────────────────────────────────────┤
│ • Spatial index (PostGIS GiST)                  │
│ • Full-text search index (Elasticsearch)        │
│ • Category indexes                              │
└─────────────────────────────────────────────────┘
```

---

## 6. סקריפטים לדוגמה

### 6.1 Python - איסוף מסעדות מ-Overpass API
```python
import requests
import json

overpass_url = "https://overpass-api.de/api/interpreter"
query = """
[out:json];
area["name"="תל אביב-יפו"]["admin_level"="8"]->.a;
(
  node["amenity"="restaurant"](area.a);
  way["amenity"="restaurant"](area.a);
);
out center;
"""

response = requests.post(overpass_url, data={'data': query})
data = response.json()

restaurants = []
for element in data['elements']:
    if 'tags' in element:
        poi = {
            'id': element['id'],
            'name': element['tags'].get('name', 'Unknown'),
            'name_he': element['tags'].get('name:he', ''),
            'lat': element.get('lat') or element['center']['lat'],
            'lon': element.get('lon') or element['center']['lon'],
            'cuisine': element['tags'].get('cuisine', ''),
            'phone': element['tags'].get('phone', ''),
            'website': element['tags'].get('website', '')
        }
        restaurants.append(poi)

# שמור ל-JSON
with open('restaurants_tlv.json', 'w', encoding='utf-8') as f:
    json.dump(restaurants, f, ensure_ascii=False, indent=2)

print(f"נאספו {len(restaurants)} מסעדות")
```

### 6.2 Python - העשרה מ-Wikipedia
```python
import requests

def get_wikipedia_summary(title):
    url = f"https://he.wikipedia.org/api/rest_v1/page/summary/{title}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        return {
            'description': data.get('extract', ''),
            'image': data.get('thumbnail', {}).get('source', ''),
            'url': data.get('content_urls', {}).get('desktop', {}).get('page', '')
        }
    return None

# דוגמה
info = get_wikipedia_summary("מגדל_דוד")
print(info)
```

---

## 7. תדירות עדכון

| מקור | תדירות עדכון מומלצת |
|------|---------------------|
| OSM | שבועי (diff updates) |
| data.gov.il | חודשי |
| Wikidata | חודשי |
| Wikipedia | לפי צורך (cache) |

**אסטרטגיית עדכון:**
1. **Initial load** - טעינה מלאה של כל הנתונים
2. **Incremental updates** - OSM daily diffs
3. **Manual refresh** - data.gov.il כל חודש
4. **On-demand** - Wikipedia עבור POI חדשים

---

## 8. אתגרים נפוצים ופתרונות

### 8.1 Encoding בעברית
**בעיה:** קבצי CSV עם encoding שגוי  
**פתרון:** 
```python
import pandas as pd
df = pd.read_csv('file.csv', encoding='utf-8-sig')
```

### 8.2 Duplicate POI
**בעיה:** אותו מקום מופיע ב-OSM ו-data.gov.il  
**פתרון:** 
- Fuzzy match על שם + כתובת
- מרחק < 50 מטר = duplicate
- בחר את הרשומה העשירה יותר

### 8.3 קואורדינטות חסרות
**בעיה:** data.gov.il לא תמיד כולל lat/lon  
**פתרון:**
- Geocode באמצעות כתובת מלאה
- Match ל-OSM לפי שם רחוב + מספר בית

### 8.4 OSM Overpass Timeout
**בעיה:** Query מורכבת = timeout  
**פתרון:**
- חלק לשאילתות קטנות יותר (לפי עיר)
- השתמש ב-PBF download + local processing

---

## 9. מדדי איכות (KPI)

| מדד | יעד | בדיקה |
|-----|-----|-------|
| כיסוי ישובים | 100% (כל 1,200 ישובים) | `SELECT COUNT(DISTINCT city) FROM poi` |
| כיסוי רחובות | 95%+ | `SELECT COUNT(*) FROM streets` |
| תמונה – אתרים ונקודות ציון | 30%+ | נקודות עם שם בתת-קטגוריות `NOTABLE_SUBS` (תיירות, טבע, רט"ג, מלונות…) עם `image` |
| תיאור – אתרים ונקודות ציון | 40%+ | אותן נקודות עם `description` |
| תמונה / תיאור לכלל הנקודות | לידיעה בלבד | רוב הנקודות (תחנות אוטובוס, חנויות, ברזיות) לעולם לא יקבלו תמונה או ערך |
| דיוק geocoding | 90%+ | בדיקה ידנית על sample |

---

*מסמך זה מספק **מפת דרכים** לאיסוף נתונים. יישום בפועל ידרוש סקריפטים, תשתית, ו-QA מתמשך.*

---

## 10. תוכן עשיר לאתרי טיול ושבילים (`hmaps.etl.rich`)

הפעלה: `enrich-content.cmd` (או `python -m hmaps.etl enrich`). רץ על המאגר הקיים, בלי לבנות מחדש את המפה,
ומפרסם גרסה חדשה של המאגר שהשרת עובר אליה לבד. גם בנייה מלאה (`update-data.cmd`) מריצה את השלב הזה
(לכיבוי: `HMAPS_RICH=0`).

| מקור | מה נלקח | רישיון | הערות |
|------|---------|--------|-------|
| Wikimedia Commons | גלריה של עד 10 תמונות לאתר ולשביל: לפי מיקום, לפי הקטגוריה של הפריט בוויקינתונים (P373) ותמונת הערך בוויקיפדיה | CC BY / CC BY-SA (לכל תמונה נשמרים צלם, רישיון וקישור – מוצגים בכרטיס) | התמונות **לא** מורדות; נטענות מהרשת. מסננים מפות, לוגואים ובתי תפילה של דתות אחרות |
| ויקיפדיה העברית | התאמת ערך לפי מיקום + שם לאתרים בלי תגית ויקיפדיה; כל פסקת הפתיחה של הערך | CC BY-SA 4.0 | ערכים לשבילים בעלי שם לפי חיפוש כותרת מדויק |
| Wikidata | מעמד (גן לאומי / שמורה / אתר מורשת), שנת הקמה, גובה, שטח, אורך, אתר רשמי, קטגוריית Commons, קישור לוויקימסע | CC0 | |
| ויקימסע (Wikivoyage) | פתיח הערך – מידע למטייל | CC BY-SA 4.0 | לפי קישור מוויקינתונים, או חיפוש לפי מיקום לאתרים בעלי ערך בוויקיפדיה |
| Mapillary (רשות) | תמונות רחוב/שביל לאתרים עם פחות מ-3 תמונות ולשבילים | CC BY-SA 4.0 | דורש מפתח חינמי: יוצרים אפליקציה ב-mapillary.com/dashboard/developers ושומרים את ה-Client Token בקובץ `data/mapillary-token.txt` (או `HMAPS_MAPILLARY_TOKEN`) |

לא נעשה שימוש ב-Google Maps / Places: התנאים שלהם אוסרים שמירה של התוכן והצגתו על מפה שאינה של Google.

* מטמון: `data/cache/rich-http.sqlite` (60 יום) – ריצה חוזרת או קטועה ממשיכה מהר.
* בדיקה מהירה: `python -m hmaps.etl enrich --limit 50 --steps wikipedia,commons`.
* תמונות לשימוש בלי אינטרנט: `download-images.cmd` מוריד גם את תמונות הגלריות. `HMAPS_REMOTE_IMAGES=0` = מצב לא-מקוון מלא (רק תמונות שהורדו).
* הכרטיס של אתר טיול מציג: גלריה עם מציג תמונות, טקסט מלא (ויקיפדיה / ויקימסע), עובדות, שבילים מסומנים שעוברים במקום ואתרים בסביבה.
* הכרטיס של שביל מציג: גלריה לאורך השביל, ערך ויקיפדיה, נקודת התחלה וסיום, מסלול מעגלי, זמן הליכה/רכיבה משוער
  (עם עליות כשנתוני הגובה מותקנים), דרגת קושי (sac_scale), ואתרים / מעיינות / ברזיות לאורך השביל לפי סדר ההליכה.
