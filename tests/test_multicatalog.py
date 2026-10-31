"""Several catalogs merged in one run.
Run:  python -m unittest discover tests
"""
import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import lrrecover  # noqa: E402
from test_lrrecover import make_catalog, ns, R  # noqa: E402


class TwoCatalogs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.photos = os.path.join(d, "photos")
        os.makedirs(os.path.join(self.photos, "2014"))
        for n in ("IMG_0001.CR2", "IMG_0001.JPG", "IMG_0002.NEF", "IMG_0003.NEF"):
            open(os.path.join(self.photos, "2014", n), "wb").close()
        # Same file name in two folders, like "Lightroom Catalog.lrcat" per year.
        self.cat_a = os.path.join(d, "cats", "2014", "Lightroom Catalog.lrcat")
        self.cat_b = os.path.join(d, "cats", "2015", "Lightroom Catalog.lrcat")
        for c in (self.cat_a, self.cat_b):
            os.makedirs(os.path.dirname(c))
            make_catalog(c, self.photos)
        con = sqlite3.connect(self.cat_b)
        con.executescript('''
        ALTER TABLE Adobe_images ADD COLUMN touchTime;
        UPDATE Adobe_images SET rating = 5, touchTime = 500000000 WHERE id_local = 3;
        UPDATE Adobe_images SET rating = NULL, colorLabels = NULL WHERE id_local = 1;
        INSERT INTO AgLibraryKeyword VALUES (4, 'Sydney', 2);
        INSERT INTO AgLibraryKeywordImage (image, tag) VALUES (3, 4);
        INSERT INTO AgLibraryFile VALUES (4, 1, 'IMG_0003', 'NEF');
        INSERT INTO Adobe_images (id_local, rootFile, fileFormat, rating) VALUES (5, 4, 'RAW', 2);
        ''')
        con.commit()
        con.close()
        # A Lightroom backup copy that must be ignored when searching folders.
        bk = os.path.join(d, "cats", "2014", "Backups", "2016-01-01 1200")
        os.makedirs(bk)
        open(os.path.join(bk, "Lightroom Catalog.lrcat"), "wb").close()
        os.makedirs(os.path.join(d, "cats", "2014", "Lightroom Catalog Previews.lrdata"))
        self.report = os.path.join(d, "report.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def tags(self, name):
        with open(os.path.join(self.photos, "2014", name), encoding="utf-8") as fh:
            text = fh.read()
        root = ET.fromstring(text.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])
        d = root.find(f".//{R}Description")
        el = d.find(ns("lr", "hierarchicalSubject"))
        tags = [li.text for li in el.iter(f"{R}li")] if el is not None else []
        return d.attrib, tags

    def test_find_catalogs_skips_backups(self):
        found = lrrecover.find_catalogs(os.path.join(self.tmp.name, "cats"))
        self.assertEqual(sorted(found), sorted([self.cat_a, self.cat_b]))

    def test_names_are_unique(self):
        names = lrrecover.catalog_names([self.cat_a, self.cat_b])
        self.assertEqual(sorted(names.values()),
                         ["2014 - Lightroom Catalog", "2015 - Lightroom Catalog"])

    def test_merge(self):
        log = []
        res = lrrecover.run(lrrecover.Options(catalogs=[self.cat_a, self.cat_b],
                                              report=self.report), log=log.append)
        text = "\n".join(log)
        self.assertIn("Catalogs: 2", text)
        self.assertIn("3 photos are in more than one catalog", text)
        self.assertEqual(res.photos, 4)  # 3 shared + IMG_0003 only in 2015

        a, t = self.tags("IMG_0002.xmp")
        self.assertEqual(a[ns("xmp", "Rating")], "5")             # newer catalog's rating
        self.assertIn("Places|Sydney", t)                          # keyword only in 2015
        self.assertIn("Lightroom|rejected", t)                     # pick from both
        self.assertIn("Lightroom|catalogs|2014 - Lightroom Catalog", t)
        self.assertIn("Lightroom|catalogs|2015 - Lightroom Catalog", t)

        a, t = self.tags("IMG_0001.xmp")
        self.assertEqual(a[ns("xmp", "Rating")], "4")             # blank in 2015, kept from 2014
        self.assertEqual(a[ns("xmp", "Label")], "Red")
        self.assertIn("Lightroom|collections|2014 - Lightroom Catalog|Best of 2014", t)
        self.assertIn("Lightroom|collections|2015 - Lightroom Catalog|Best of 2014", t)
        self.assertEqual(t.count("Places|Beach"), 1)               # no duplicates

        a, t = self.tags("IMG_0003.xmp")
        self.assertEqual(a[ns("xmp", "Rating")], "2")
        self.assertEqual([x for x in t if x.startswith("Lightroom|catalogs|")],
                         ["Lightroom|catalogs|2015 - Lightroom Catalog"])

    def test_cli_with_folder(self):
        rc = lrrecover.main([os.path.join(self.tmp.name, "cats"), "--report", self.report, "--yes"])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(self.photos, "2014", "IMG_0003.xmp")))

    def test_single_catalog_has_no_catalog_prefix(self):
        lrrecover.run(lrrecover.Options(catalog=self.cat_a, report=self.report), log=lambda m: None)
        _, t = self.tags("IMG_0001.xmp")
        self.assertIn("Lightroom|collections|Best of 2014", t)
        self.assertFalse(any(x.startswith("Lightroom|catalogs|") for x in t))


if __name__ == "__main__":
    unittest.main()
