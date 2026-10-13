import unittest
from datetime import datetime

from hmaps.core import categories, geo, hebrew, hours, query


class HebrewNormalizationTest(unittest.TestCase):
    def test_final_letters_and_niqqud(self):
        self.assertEqual(hebrew.normalize("שָׁלוֹם"), "שלומ")
        self.assertEqual(hebrew.normalize("ירושלים"), hebrew.normalize("ירושלַיִם"))

    def test_quotes_and_maqaf(self):
        self.assertEqual(hebrew.normalize('ת"א-יפו'), "תא יפו")
        self.assertEqual(hebrew.normalize("רח׳ הרצל"), "רח הרצל")
        self.assertEqual(hebrew.normalize("בית־שאן"), "בית שאנ")

    def test_full_and_defective_spelling(self):
        self.assertEqual(hebrew.normalize("קריית שמונה"), hebrew.normalize("קרית שמונה"))
        self.assertEqual(hebrew.normalize("פתח תקווה"), hebrew.normalize("פתח תקוה"))

    def test_latin(self):
        self.assertEqual(hebrew.normalize("Café  TEL-Aviv"), "cafe tel aviv")

    def test_prefix_stripping(self):
        self.assertIn("תל", hebrew.strip_prefixes("בתל"))
        self.assertIn("מוזיאונ", hebrew.strip_prefixes("והמוזיאונ"))
        self.assertEqual(hebrew.strip_prefixes("בת"), ["בת"])  # too short to strip

    def test_abbreviations(self):
        self.assertEqual(hebrew.query_tokens('ת"א'), ["תל", "אביב"])
        self.assertEqual(hebrew.query_tokens("רחוב הרצל"), ["הרצל"])
        self.assertEqual(hebrew.query_tokens("שד' רוטשילד"), ["שדרות", "רוטשילד"])

    def test_keyboard_layout(self):
        self.assertEqual(hebrew.fix_keyboard_layout("shzbdu;"), "דיזנגוף")
        self.assertEqual(hebrew.fix_keyboard_layout("יקךךם"), "hello")
        self.assertIsNone(hebrew.fix_keyboard_layout("123"))

    def test_edit_distance(self):
        self.assertEqual(hebrew.edit_distance("דיזנגופ", "דיזנגופ"), 0)
        self.assertEqual(hebrew.edit_distance("דיזנגופ", "דיזינגופ"), 1)
        self.assertEqual(hebrew.edit_distance("טבריה", "תבריה"), 0.5)  # ט/ת confusion
        self.assertEqual(hebrew.edit_distance("ab", "ba"), 1)  # transposition
        self.assertGreater(hebrew.edit_distance("חיפה", "אילת", limit=1), 1)

    def test_format(self):
        self.assertEqual(hebrew.format_distance(447), "450 מ׳")
        self.assertEqual(hebrew.format_distance(3240), "3.2 ק״מ")
        self.assertEqual(hebrew.format_duration(65 * 60), "שעה ו-5 דק׳")
        self.assertEqual(hebrew.format_duration(120 * 60), "שעתיים")


class CategoriesTest(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(categories.classify({"amenity": "restaurant", "name": "x"}), ("food", "restaurant"))
        self.assertEqual(categories.classify({"amenity": "place_of_worship", "religion": "jewish"}), ("religion", "synagogue"))
        self.assertIsNone(categories.classify({"amenity": "place_of_worship", "religion": "christian", "name": "x"}))
        self.assertEqual(categories.classify({"amenity": "place_of_worship", "name": "בית הכנסת הגדול"}), ("religion", "synagogue"))
        self.assertEqual(categories.classify({"amenity": "school", "name": "ישיבת חברון"}), ("religion", "yeshiva"))
        self.assertEqual(categories.classify({"amenity": "school", "name": "בית ספר אורט"}), ("education", "school"))
        self.assertEqual(categories.classify({"railway": "station", "station": "light_rail", "name": "x"}), ("transport", "light_rail"))
        self.assertEqual(categories.classify({"railway": "station", "name": "x"}), ("transport", "train_station"))
        self.assertIsNone(categories.classify({"amenity": "restaurant"}))  # unnamed
        self.assertEqual(categories.classify({"amenity": "atm"}), ("shopping", "atm"))
        self.assertIsNone(categories.classify({"amenity": "nightclub", "name": "x"}))
        self.assertIsNone(categories.classify({"shop": "alcohol", "name": "x"}))
        self.assertIsNone(categories.classify({"amenity": "parking"}))  # unnamed small lot
        self.assertEqual(categories.classify({"amenity": "parking", "parking": "underground"}), ("transport", "parking"))

    def test_kosher(self):
        self.assertTrue(categories.is_kosher({"diet:kosher": "yes"}))
        self.assertTrue(categories.is_kosher({"cuisine": "kosher;pizza"}))
        self.assertIsNone(categories.is_kosher({}))

    def test_phrases(self):
        self.assertEqual(categories.PHRASES["מסעדות"], ("food", "restaurant"))
        self.assertEqual(categories.PHRASES[hebrew.normalize("בתי קפה")], ("food", "cafe"))
        self.assertEqual(categories.PHRASES[hebrew.normalize("בית מרקחת")], ("health", "pharmacy"))

    def test_marker_colors_contrast(self):
        # White icons on category colors must reach WCAG 3:1 for graphics.
        def lum(hex_):
            c = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        for cat in categories.CATEGORIES:
            ratio = 1.05 / (lum(cat.color) + 0.05)
            self.assertGreaterEqual(ratio, 3.0, cat.id)


class HoursTest(unittest.TestCase):
    def test_israeli_week(self):
        w = hours.parse("Su-Th 08:00-20:00; Fr 08:00-14:00; Sa off")
        self.assertEqual(w[0], [(480, 1200)])
        self.assertEqual(w[5], [(480, 840)])
        self.assertEqual(w[6], [])

    def test_split_and_overnight(self):
        w = hours.parse("Mo-Th 08:00-13:00,16:00-19:00; Sa 20:00-02:00")
        self.assertEqual(w[1], [(480, 780), (960, 1140)])
        self.assertEqual(w[6], [(1200, 1560)])
        # Sunday 01:00 is still Saturday night's opening
        sunday_1am = datetime(2026, 10, 11, 1, 0, tzinfo=hours.TZ)
        self.assertTrue(hours.is_open(w, sunday_1am))

    def test_24_7(self):
        w = hours.parse("24/7")
        self.assertTrue(hours.is_open(w, datetime(2026, 10, 10, 3, 0, tzinfo=hours.TZ)))
        self.assertEqual(hours.describe_he(w)[0]["text"], "פתוח 24 שעות")

    def test_unsupported_returns_none(self):
        self.assertIsNone(hours.parse("Jan-Mar Mo-Fr 08:00-12:00"))
        self.assertIsNone(hours.parse("sunrise-sunset"))
        self.assertIsNone(hours.parse(""))

    def test_next_change(self):
        w = hours.parse("Su-Th 08:00-20:00")
        t = datetime(2026, 10, 12, 19, 10, tzinfo=hours.TZ)  # Monday
        self.assertTrue(hours.is_open(w, t))
        self.assertEqual(hours.next_change(w, t).strftime("%H:%M"), "20:00")

    def test_times_without_days(self):
        w = hours.parse("09:00-17:00")
        self.assertEqual(w[6], [(540, 1020)])


class QueryParserTest(unittest.TestCase):
    def test_category_in_city(self):
        q = query.parse("מסעדות בתל אביב")
        self.assertEqual((q.category, q.sub), ("food", "restaurant"))
        self.assertEqual(q.location_text, "תל אביב")

    def test_category_near_me_with_flags(self):
        q = query.parse("בית מרקחת פתוח ליד")
        self.assertEqual(q.sub, "pharmacy")
        self.assertTrue(q.open_now)
        self.assertTrue(q.near_me)
        q = query.parse("מסעדה כשרה באזור חיפה")
        self.assertTrue(q.kosher)
        self.assertEqual(q.location_text, "חיפה")

    def test_suffix_category(self):
        q = query.parse("חיפה בתי קפה")
        self.assertEqual(q.sub, "cafe")
        self.assertEqual(q.location_text, "חיפה")

    def test_name_with_category_word_is_not_category(self):
        q = query.parse("קפה לנדוור")
        self.assertIsNone(q.category)
        self.assertEqual(q.category_hint, "cafe")

    def test_address(self):
        q = query.parse("רח' הרצל 12, רחובות")
        self.assertTrue(q.is_address)
        self.assertEqual((q.street_text, q.house_number, q.city_text), ("הרצל", "12", "רחובות"))
        q = query.parse("דיזנגוף 50 תל אביב")
        self.assertEqual((q.street_text, q.house_number, q.city_text), ("דיזנגופ", "50", "תל אביב"))
        q = query.parse("ירושלים, יפו 97")
        self.assertEqual((q.street_text, q.house_number, q.city_text), ("יפו", "97", "ירושלימ"))

    def test_coordinates(self):
        self.assertEqual(query.parse("32.0853, 34.7818").coords, (32.0853, 34.7818))
        self.assertEqual(query.parse("34.7818 32.0853").coords, (32.0853, 34.7818))


class GeoTest(unittest.TestCase):
    def test_haversine(self):
        d = geo.haversine(32.0853, 34.7818, 32.0861, 34.7792)
        self.assertAlmostEqual(d, 260, delta=10)

    def test_polygon(self):
        sq = [[(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]]
        self.assertEqual(geo.polygon_centroid([sq]), (1.0, 1.0))
        self.assertTrue(geo.point_in_polygons(1, 1, [sq]))
        hole = [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5), (0.5, 0.5)]
        self.assertFalse(geo.point_in_polygons(1, 1, [[sq[0], hole]]))
        x, y = geo.representative_point([[sq[0], hole]])
        self.assertTrue(geo.point_in_polygons(x, y, [[sq[0], hole]]))

    def test_polyline_roundtrip(self):
        coords = [(34.7818, 32.0853), (34.7792, 32.0861), (35.2281, 31.7767)]
        back = geo.decode_polyline(geo.encode_polyline(coords))
        for (a, b), (c, d) in zip(coords, back):
            self.assertAlmostEqual(a, c, places=5)
            self.assertAlmostEqual(b, d, places=5)

    def test_tiles(self):
        x, y = geo.lonlat_to_tile(34.7818, 32.0853, 14)
        minx, miny, maxx, maxy = geo.tile_bounds(14, x, y)
        self.assertTrue(minx <= 34.7818 <= maxx and miny <= 32.0853 <= maxy)


class OutdoorTest(unittest.TestCase):
    """Hiker POIs, INPA detection, trail marking colours and geometry assembly."""

    def test_new_poi_types(self):
        c = categories.classify
        self.assertEqual(c({"waterway": "stream", "name": "נחל עמוד"}), ("nature", "stream"))
        self.assertEqual(c({"waterway": "wadi", "name:he": "ואדי קלט"}), ("nature", "stream"))
        self.assertIsNone(c({"waterway": "stream"}))  # unnamed
        self.assertEqual(c({"natural": "ridge", "name": "רכס"}), ("nature", "ridge"))
        self.assertEqual(c({"natural": "saddle", "name": "x"}), ("nature", "saddle"))
        self.assertEqual(c({"natural": "cliff", "name": "x"}), ("nature", "cliff"))
        self.assertEqual(c({"natural": "gorge", "name": "x"}), ("nature", "valley"))
        self.assertEqual(c({"amenity": "drinking_water"}), ("facilities", "drinking_water"))
        self.assertEqual(c({"amenity": "toilets"}), ("facilities", "toilets"))
        self.assertIsNone(c({"amenity": "toilets", "access": "customers"}))
        self.assertEqual(c({"amenity": "shelter", "shelter_type": "basic_hut"}), ("facilities", "shelter"))
        self.assertIsNone(c({"amenity": "shelter", "shelter_type": "public_transport"}))
        self.assertEqual(c({"tourism": "information", "information": "board", "name": "x"}), ("facilities", "trail_info"))
        self.assertIsNone(c({"tourism": "information", "information": "guidepost"}))  # unnamed
        self.assertEqual(c({"tourism": "information", "information": "office", "name": "x"}), ("tourism", "tourist_info"))
        self.assertEqual(c({"man_made": "water_tower", "name": "x"}), ("facilities", "water_tower"))
        self.assertEqual(c({"man_made": "water_well"}), ("nature", "well"))
        self.assertEqual(c({"historic": "memorial", "name": "x"}), ("tourism", "memorial"))
        self.assertEqual(c({"historic": "monument", "name": "x"}), ("tourism", "historic"))
        self.assertEqual(c({"office": "lawyer", "name": "x"}), ("shopping", "office"))
        self.assertEqual(c({"office": "government", "name": "x"}), ("government", "government_office"))
        self.assertEqual(c({"craft": "carpenter", "name": "x"}), ("shopping", "craft"))
        self.assertIsNone(c({"craft": "winery", "name": "x"}))  # same policy as alcohol shops
        self.assertEqual(categories.kind_he({"office": "lawyer"}, "office"), "עורך דין")
        self.assertEqual(categories.kind_he({"shop": "books"}, "shop"), "ספרים")

    def test_inpa_detection(self):
        src = categories.inpa_source
        self.assertEqual(src({"operator": "Israel Nature and Parks Authority"}), "operator")
        self.assertEqual(src({"operator": "רשות הטבע והגנים"}), "operator")
        self.assertEqual(src({"operator:he": "רשות הטבע והגנים הלאומיים"}), "operator")
        self.assertEqual(src({"operator": "INPA"}), "operator")
        self.assertEqual(src({"name": "גן לאומי קיסריה"}), "name")
        self.assertEqual(src({"name": "שמורת טבע עין אפק"}), "name")
        self.assertIsNone(src({"name": "שמורת טבע עין אפק", "operator": "קרן קיימת לישראל"}))
        self.assertIsNone(src({"name": "פארק הירקון"}))
        c = categories.classify
        self.assertEqual(c({"boundary": "national_park", "name": "גן לאומי קיסריה"}), ("nature", "inpa_site"))
        self.assertEqual(c({"leisure": "nature_reserve", "name": "x", "operator": "npa"}), ("nature", "inpa_site"))
        self.assertEqual(c({"leisure": "nature_reserve", "name": "שמורת הבניאס"}), ("nature", "nature_reserve"))
        self.assertIsNone(c({"tourism": "attraction", "operator": "INPA"}))  # unnamed attraction
        self.assertEqual(categories.PHRASES[hebrew.normalize('אתרי רט"ג')], ("nature", "inpa_site"))
        self.assertEqual(categories.PHRASES[hebrew.normalize("נחלים")], ("nature", "stream"))
        self.assertEqual(categories.expand_sub("nature_reserve"), ("nature_reserve", "inpa_site"))
        # a stream name is a name query (with a hint), not a category search
        q = query.parse("נחל עמוד")
        self.assertIsNone(q.category)
        self.assertEqual(q.category_hint, "stream")

    def test_trail_marking(self):
        from hmaps.etl.trails import display_name, marking
        self.assertEqual(marking({"osmc:symbol": "blue:white:blue_stripe"}), "blue")
        self.assertEqual(marking({"osmc:symbol": "orange:orange:white_right:blue_stripe", "network": "nwn"}), "orange")
        self.assertEqual(marking({"osmc:symbol": "white:white:red_stripe"}), "red")
        self.assertEqual(marking({"colour": "#e11e26"}), "red")
        self.assertEqual(marking({"colour": "#800080"}), "purple")
        self.assertEqual(marking({"colour": "maroon"}), "red")
        self.assertEqual(marking({"network": "rwn"}), "purple")
        self.assertEqual(marking({"network": "lwn"}), "other")
        self.assertEqual(display_name(None, "hiking", "green"), "שביל מסומן ירוק")
        self.assertEqual(display_name(None, "mtb", "other"), "מסלול אופניים מסומן")

    def test_trail_assembly(self):
        from hmaps.etl.trails import TrailCollector, chain, components
        a, b, c, d = (35.0, 31.0), (35.01, 31.0), (35.02, 31.0), (35.03, 31.0)
        # out of order and reversed members still make one line
        lines = chain([[c, d], [b, a], [b, c]])
        self.assertEqual(len(lines), 1)
        self.assertEqual({lines[0][0], lines[0][-1]}, {a, d})
        self.assertEqual(components([[a, b], [c, d]]), 2)
        self.assertEqual(components([[a, b], [b, c], [(35.015, 31.01), b]]), 1)  # T junction
        tc = TrailCollector()
        self.assertTrue(tc.add_relation(1, {"type": "route", "route": "hiking", "name": "x", "network": "lwn"},
                                        [("w", 10), ("w", 11), ("w", 10), ("n", 5)]))
        self.assertFalse(tc.add_relation(2, {"type": "route", "route": "bus"}, [("w", 10)]))
        self.assertFalse(tc.add_relation(3, {"type": "route", "route": "hiking", "state": "proposed"}, [("w", 10)]))
        tc.add_relation(4, {"type": "superroute", "route": "bicycle", "name": "all"}, [("r", 5)])
        tc.add_relation(5, {"type": "route", "route": "bicycle", "name": "part"}, [("w", 11)])
        self.assertEqual(tc.wanted(), {10, 11})
        self.assertEqual(tc.marked_profiles(10), frozenset({"foot"}))
        self.assertEqual(tc.marked_profiles(11), frozenset({"foot", "bike"}))
        tc.add_way(10, [a, b])
        tc.add_way(11, [b, c])
        tc.add_way(99, [c, d])  # not a member: ignored
        trails = {t["osm"]: t for t in tc.build()}
        self.assertEqual(set(trails), {"r1", "r4", "r5"})
        one = trails["r1"]
        self.assertAlmostEqual(one["length_m"], geo.line_length_m([a, b, c]), delta=1)  # way 10 listed twice counts once
        self.assertEqual(one["parts"], 1)
        self.assertAlmostEqual(trails["r4"]["length_m"], geo.line_length_m([b, c]), delta=1)  # via the child relation

    def test_opl_relation(self):
        from hmaps.etl.trails import parse_opl_relation
        rid, tags, members = parse_opl_relation(
            "r282071 v9 dV c1 t2024-01-01T00:00:00Z i1 uX Ttype=route,route=hiking,name=%5e9%%5d1%%5d9%%5dc%%20%%5d9%%5e9%%5e8%%5d0%%5dc%,"
            "osmc:symbol=orange:white:orange_stripe Mw10@,w11@forward,n5@guidepost")
        self.assertEqual(rid, 282071)
        self.assertEqual(tags["name"], "שביל ישראל")
        self.assertEqual(tags["osmc:symbol"], "orange:white:orange_stripe")
        self.assertEqual(members, [("w", 10), ("w", 11), ("n", 5)])

    def test_simplify_and_polygon_tile(self):
        line = [(35.0, 31.0), (35.00001, 31.000001), (35.01, 31.0)]
        self.assertEqual(geo.simplify(line, 5), [(35.0, 31.0), (35.01, 31.0)])
        from hmaps.api import mvt
        layer = mvt.Layer("parks")
        # clockwise-in-screen exterior given counter-clockwise: winding is fixed by the encoder
        self.assertTrue(layer.add_polygon([[[(0, 0), (0, 100), (100, 100), (100, 0), (0, 0)]]], {"name": "x"}))
        self.assertFalse(layer.add_polygon([[[(0, 0), (0, 0), (0, 0)]]], {}))
        feat = mvt.decode(mvt.encode_tile([layer]))["parks"][0]
        self.assertEqual(feat["type"], 3)
        self.assertEqual(feat["geometry"][-1], 15)  # ClosePath


if __name__ == "__main__":
    unittest.main()
