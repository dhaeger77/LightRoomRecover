"""Smart collections and the organization summary.
Run:  python -m unittest discover tests
"""
import datetime as dt
import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import lrrecover  # noqa: E402
import smartcollections as sc  # noqa: E402
from test_lrrecover import make_catalog, ns, R  # noqa: E402

SMART = {
    # id: (name, parent, lua)
    10: ("Top rated", None, '''s = {
        { criteria = "rating", operation = ">=", value = 4, value2 = 0, },
        combine = "intersect", }'''),
    11: ("Beach or rejected", 20, '''s = {
        { criteria = "keywords", operation = "any", value = "beach", value2 = "", },
        { criteria = "pick", operation = "==", value = -1, },
        combine = "union", }'''),
    12: ("2014 but not 4 stars", None, '''s = {
        { criteria = "captureTime", operation = "in", value = "2014-01-01", value2 = "2014-12-31", },
        { { criteria = "rating", operation = "==", value = 4, }, combine = "exclude", },
        combine = "intersect", }'''),
    13: ("Faces", None, '''s = {
        { criteria = "face", operation = "any", value = "Bob", },
        combine = "intersect", }'''),
    14: ("Anything in Places", None, '''s = {
        { criteria = "keywords", operation = "words", value = "places", },
        combine = "intersect", }'''),
}


def add_smart_collections(cat):
    con = sqlite3.connect(cat)
    con.executescript('''
    CREATE TABLE AgLibraryCollectionContent (id_local INTEGER PRIMARY KEY, collection,
        content, owningModule);
    ALTER TABLE Adobe_images ADD COLUMN captureTime;
    ''')
    con.execute("INSERT INTO AgLibraryCollection VALUES (20, 'Saved searches', NULL, "
                "'com.adobe.ag.library.group')")
    for cid, (name, parent, lua) in SMART.items():
        con.execute("INSERT INTO AgLibraryCollection VALUES (?, ?, ?, ?)",
                    (cid, name, parent, "com.adobe.ag.library.smart_collection"))
        con.execute("INSERT INTO AgLibraryCollectionContent (collection, content, owningModule) "
                    "VALUES (?, ?, 'ag.library.smart_collection')", (cid, lua))
    con.execute("UPDATE Adobe_images SET captureTime = '2014-06-01T10:00:00' WHERE id_local IN (1, 3)")
    con.commit()
    con.close()


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        os.makedirs(os.path.join(d, "photos", "2014"))
        for n in ("IMG_0001.CR2", "IMG_0001.JPG", "IMG_0002.NEF"):
            open(os.path.join(d, "photos", "2014", n), "wb").close()
        self.cat = os.path.join(d, "test.lrcat")
        make_catalog(self.cat, os.path.join(d, "photos"))
        add_smart_collections(self.cat)
        self.report = os.path.join(d, "report.csv")
        self.log = []
        self.res = lrrecover.run(lrrecover.Options(catalog=self.cat, report=self.report),
                                 log=self.log.append)

    def tearDown(self):
        self.tmp.cleanup()

    def tags(self, name):
        path = os.path.join(self.tmp.name, "photos", "2014", name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        root = ET.fromstring(text.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])
        el = root.find(f".//{ns('lr', 'hierarchicalSubject')}")
        return [li.text for li in el.iter(f"{R}li")] if el is not None else []

    def test_smart_collection_tags(self):
        t1, t2 = self.tags("IMG_0001.xmp"), self.tags("IMG_0002.xmp")
        p = "Lightroom|smart collections|"
        self.assertIn(p + "Top rated", t1)                      # rating 4
        self.assertNotIn(p + "Top rated", t2)
        self.assertIn(p + "Saved searches|Beach or rejected", t1)   # keyword Beach
        self.assertIn(p + "Saved searches|Beach or rejected", t2)   # rejected
        self.assertIn(p + "2014 but not 4 stars", t2)           # nested exclude group
        self.assertNotIn(p + "2014 but not 4 stars", t1)
        self.assertIn(p + "Anything in Places", t1)             # parent keyword matches child
        self.assertFalse(any("Faces" in t for t in t1 + t2))    # unsupported: not tagged
        # Smart collections are not also exported as normal collections.
        self.assertFalse(any(t.startswith("Lightroom|collections|Top rated") for t in t1))

    def test_listing_file(self):
        with open(self.res.smart_listing, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Top rated  [tagged 1 photo]", text)
        self.assertIn("Saved searches > Beach or rejected  [tagged 2 photos]", text)
        self.assertIn("Faces  [not tagged: can't evaluate criteria 'face']", text)
        self.assertIn("rating >= 4", text)
        self.assertIn("Match NONE of:", text)

    def test_summary(self):
        s = self.res.summary
        self.assertEqual(s["photos"], 3)
        self.assertEqual(s["rated"], 1)
        self.assertEqual(s["stars"][4], 1)
        self.assertEqual(s["labels"], {"Red": 1})
        self.assertEqual((s["picked"], s["rejected"]), (1, 1))
        self.assertEqual(s["keyword_photos"], 1)
        self.assertEqual(s["collections"], 1)
        self.assertEqual((s["smart_total"], s["smart_tagged"]), (5, 4))
        text = "\n".join(self.log)
        self.assertIn("Organization found in the catalog:", text)
        self.assertIn("Star ratings:        1 (33%)   (4 stars: 1)", text)

    def test_can_turn_off(self):
        lrrecover.run(lrrecover.Options(catalog=self.cat, report=self.report, overwrite=True,
                                        smart_collections=False), log=lambda m: None)
        self.assertFalse(any("smart collections" in t for t in self.tags("IMG_0001.xmp")))


class Photo:
    def __init__(self, **kw):
        self.rating = kw.get("rating", 0)
        self.label = kw.get("label", "")
        self.pick = kw.get("pick", 0)
        self.keywords = kw.get("keywords", [])
        self.collections = kw.get("collections", [])
        self.file_format = kw.get("file_format", "RAW")
        self.facts = kw.get("facts", {})
        self.smart_collections = []


TODAY = dt.date(2026, 10, 31)


def ev(rule, photo):
    return sc.eval_rule(rule, photo, TODAY)


class Rules(unittest.TestCase):
    def test_text_ops(self):
        p = Photo(facts={"camera": "Canon EOS 5D Mark III"})
        self.assertTrue(ev({"criteria": "camera", "operation": "any", "value": "nikon 5d"}, p))
        self.assertFalse(ev({"criteria": "camera", "operation": "all", "value": "nikon 5d"}, p))
        self.assertTrue(ev({"criteria": "camera", "operation": "beginsWith", "value": "eos"}, p))
        self.assertTrue(ev({"criteria": "camera", "operation": "noneOf", "value": "sony"}, p))
        self.assertTrue(ev({"criteria": "camera", "operation": "==", "value": "canon eos 5d mark iii"}, p))
        self.assertTrue(ev({"criteria": "lens", "operation": "empty", "value": ""}, p))

    def test_keyword_words(self):
        p = Photo(keywords=["Sport|Professional sport"])
        self.assertTrue(ev({"criteria": "keywords", "operation": "words", "value": "sport"}, p))
        self.assertFalse(ev({"criteria": "keywords", "operation": "words", "value": "sporting"}, p))
        self.assertTrue(ev({"criteria": "keywords", "operation": "notEmpty"}, p))
        self.assertTrue(ev({"criteria": "keywords", "operation": "empty"}, Photo()))

    def test_labels(self):
        p = Photo(label="Red")
        self.assertTrue(ev({"criteria": "labelColor", "operation": "==", "value": "red"}, p))
        self.assertTrue(ev({"criteria": "labelColor", "operation": "==", "value": 1}, p))
        self.assertTrue(ev({"criteria": "labelText", "operation": "!=", "value": "none"}, p))
        self.assertTrue(ev({"criteria": "labelColor", "operation": "==", "value": "none"}, Photo()))

    def test_rating_null_is_zero(self):
        self.assertTrue(ev({"criteria": "rating", "operation": "<", "value": 1}, Photo(rating=None)))

    def test_dates(self):
        p = Photo(facts={"capture_time": "2026-10-20T08:00:00.000"})
        self.assertTrue(ev({"criteria": "captureTime", "operation": "inLast", "value": 2,
                            "_units": "weeks"}, p))
        self.assertFalse(ev({"criteria": "captureTime", "operation": "inLast", "value": 3,
                             "_units": "days"}, p))
        self.assertTrue(ev({"criteria": "captureTime", "operation": "thisYear"}, p))
        self.assertTrue(ev({"criteria": "captureTime", "operation": ">", "value": "2026-01-01"}, p))
        # touchTime is seconds since 2001-01-01
        t = (dt.datetime(2015, 3, 2) - dt.datetime(2001, 1, 1)).total_seconds()
        p = Photo(facts={"touch_time": t})
        self.assertTrue(ev({"criteria": "touchTime", "operation": "==", "value": "2015-03-02"}, p))

    def test_shutter_is_inverted(self):
        p = Photo(facts={"shutter": 7})  # 1/128 s
        # "faster than 1/8 s" in Lightroom's terms: shutterSpeed < 3 after inversion
        self.assertTrue(ev({"criteria": "shutterSpeed", "operation": "<", "value": 3}, p))

    def test_numbers_and_booleans(self):
        p = Photo(facts={"iso": 3200, "has_gps": 1, "focal_length": 50})
        self.assertTrue(ev({"criteria": "isoSpeedRating", "operation": "in", "value": 1600,
                            "value2": 6400}, p))
        self.assertTrue(ev({"criteria": "hasGPSData", "operation": "==", "value": True}, p))
        self.assertFalse(ev({"criteria": "hasGPSData", "operation": "==", "value": False}, p))
        self.assertTrue(ev({"criteria": "flashFired", "operation": "isFalse"}, p))

    def test_unsupported(self):
        with self.assertRaises(sc.Unsupported):
            ev({"criteria": "face", "operation": "any", "value": "x"}, Photo())
        with self.assertRaises(sc.Unsupported):
            ev({"criteria": "rating", "operation": "weird", "value": 1}, Photo(rating=2))

    def test_unreadable_rules_listed(self):
        res = sc.evaluate([{"path": "Broken", "rules": None, "error": "bad lua"}], [Photo()])
        self.assertEqual(res[0]["status"], "unreadable")


if __name__ == "__main__":
    unittest.main()
