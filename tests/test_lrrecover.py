"""Builds a tiny fake Lightroom catalog and checks the sidecars lrrecover writes.
Run:  python -m unittest discover tests
"""
import os
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lrrecover  # noqa: E402

DEV_TEXT = '''s = { AutoGrayscaleMix = true,
	Blacks2012 = -12,
	CropAngle = 1.5,
	CropBottom = 0.9,
	CropLeft = 0.05,
	CropRight = 0.95,
	CropTop = 0.1,
	Exposure2012 = 0.65,
	HasCrop = true,
	ProcessVersion = "6.7",
	RetouchInfo = { "centerX = 0.5, centerY = 0.4, radius = 0.02, sourceState = sourceSetExplicitly, sourceX = 0.6, sourceY = 0.4, spotType = heal", },
	Temperature = 5600,
	ToneCurveName2012 = "Medium \\"Contrast\\"",
	ToneCurvePV2012 = { 0, 0, 64, 60, 255, 255, },
	WhiteBalance = "Custom",
	Look = { Name = "Adobe Color", Parameters = { Version = 14, }, },
}'''

STORED_XMP = '''<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:tiff="http://ns.adobe.com/tiff/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 tiff:Orientation="6" tiff:ImageWidth="6000" tiff:ImageLength="4000">
<dc:title><rdf:Alt><rdf:li xml:lang="x-default">Sunset &amp; pier</rdf:li></rdf:Alt></dc:title>
</rdf:Description></rdf:RDF></x:xmpmeta>'''


def make_catalog(path, photo_dir):
    con = sqlite3.connect(path)
    con.executescript('''
    CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY, absolutePath, name);
    CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY, rootFolder, pathFromRoot);
    CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, folder, baseName, extension);
    CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rootFile, masterImage, fileFormat,
        rating, colorLabels, pick, orientation, fileWidth, fileHeight);
    CREATE TABLE Adobe_imageDevelopSettings (id_local INTEGER PRIMARY KEY, image, text);
    CREATE TABLE Adobe_AdditionalMetadata (id_local INTEGER PRIMARY KEY, image, xmp);
    CREATE TABLE AgLibraryKeyword (id_local INTEGER PRIMARY KEY, name, parent);
    CREATE TABLE AgLibraryKeywordImage (id_local INTEGER PRIMARY KEY, image, tag);
    CREATE TABLE AgLibraryCollection (id_local INTEGER PRIMARY KEY, name, parent, creationId);
    CREATE TABLE AgLibraryCollectionImage (id_local INTEGER PRIMARY KEY, image, collection);
    CREATE TABLE AgHarvestedExifMetadata (id_local INTEGER PRIMARY KEY, image, gpsLatitude, gpsLongitude);
    ''')
    root = photo_dir.replace("\\", "/").rstrip("/") + "/"
    con.execute("INSERT INTO AgLibraryRootFolder VALUES (1, ?, 'photos')", (root,))
    con.execute("INSERT INTO AgLibraryFolder VALUES (1, 1, '2014/')")
    con.executemany("INSERT INTO AgLibraryFile VALUES (?,?,?,?)", [
        (1, 1, "IMG_0001", "CR2"), (2, 1, "IMG_0001", "JPG"), (3, 1, "IMG_0002", "NEF")])
    con.executemany("INSERT INTO Adobe_images VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, 1, None, "RAW", 4, "Red", 1, "DA", 6000, 4000),
        (2, 2, None, "JPG", 0, None, 0, "AB", 6000, 4000),
        (3, 3, None, "RAW", None, None, -1, "AB", 6000, 4000),
        (4, 1, 1, "RAW", 0, None, 0, "AB", 6000, 4000)])  # virtual copy of 1
    con.execute("INSERT INTO Adobe_imageDevelopSettings (image, text) VALUES (1, ?)", (DEV_TEXT,))
    blob = len(STORED_XMP).to_bytes(4, "big") + zlib.compress(STORED_XMP.encode())
    con.execute("INSERT INTO Adobe_AdditionalMetadata (image, xmp) VALUES (1, ?)", (blob,))
    con.executemany("INSERT INTO AgLibraryKeyword VALUES (?,?,?)",
                    [(1, None, None), (2, "Places", 1), (3, "Beach", 2)])
    con.execute("INSERT INTO AgLibraryKeywordImage (image, tag) VALUES (1, 3)")
    con.execute("INSERT INTO AgLibraryCollection VALUES (1, 'Best of 2014', NULL, "
                "'com.adobe.ag.library.collection')")
    con.execute("INSERT INTO AgLibraryCollectionImage (image, collection) VALUES (1, 1)")
    con.execute("INSERT INTO AgHarvestedExifMetadata (image, gpsLatitude, gpsLongitude) "
                "VALUES (3, -33.8568, 151.2153)")
    con.commit()
    con.close()


R = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"


def ns(prefix, name):
    return "{%s}%s" % (lrrecover.NS[prefix], name)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        os.makedirs(os.path.join(d, "photos", "2014"))
        for n in ("IMG_0001.CR2", "IMG_0001.JPG", "IMG_0002.NEF"):
            open(os.path.join(d, "photos", "2014", n), "wb").close()
        self.cat = os.path.join(d, "test.lrcat")
        make_catalog(self.cat, os.path.join(d, "photos"))
        self.report = os.path.join(d, "report.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *extra):
        return lrrecover.main([self.cat, "--report", self.report, "--yes", *extra])

    def read(self, name):
        path = os.path.join(self.tmp.name, "photos", "2014", name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        return text, ET.fromstring(text.split("?>", 1)[1].rsplit("<?xpacket", 1)[0])

    def test_sidecar_contents(self):
        self.assertEqual(self.run_tool(), 0)
        text, root = self.read("IMG_0001.xmp")
        d = root.find(f".//{R}Description")
        a = d.attrib
        self.assertEqual(a[ns("crs", "Exposure2012")], "0.65")
        self.assertEqual(a[ns("crs", "HasCrop")], "True")
        self.assertEqual(a[ns("crs", "CropAngle")], "1.5")
        self.assertEqual(a[ns("crs", "ToneCurveName2012")], 'Medium "Contrast"')
        self.assertEqual(a[ns("tiff", "Orientation")], "6")
        self.assertEqual(a[ns("tiff", "ImageWidth")], "6000")
        self.assertEqual(a[ns("xmp", "Rating")], "4")
        self.assertEqual(a[ns("xmp", "Label")], "Red")
        curve = [li.text for li in d.find(ns("crs", "ToneCurvePV2012")).iter(f"{R}li")]
        self.assertEqual(curve, ["0, 0", "64, 60", "255, 255"])
        retouch = [li.text for li in d.find(ns("crs", "RetouchInfo")).iter(f"{R}li")]
        self.assertTrue(retouch[0].startswith("centerX = 0.5"))
        tags = [li.text for li in d.find(ns("lr", "hierarchicalSubject")).iter(f"{R}li")]
        self.assertIn("Places|Beach", tags)
        self.assertIn("Lightroom|collections|Best of 2014", tags)
        self.assertIn("Lightroom|picked", tags)
        self.assertIn("Lightroom|had virtual copies", tags)
        title = [li.text for li in d.find(ns("dc", "title")).iter(f"{R}li")]
        self.assertEqual(title, ["Sunset & pier"])
        self.assertIn('softwareAgent="Adobe Photoshop Lightroom', text)
        # darktable walks list children via whitespace text nodes; keep them indented.
        self.assertIn("<crs:ToneCurvePV2012>\n    <rdf:Seq>\n", text)

    def test_gps_and_reject(self):
        self.run_tool()
        _, root = self.read("IMG_0002.xmp")
        a = root.find(f".//{R}Description").attrib
        self.assertEqual(a[ns("exif", "GPSLatitude")], "33,51.408000S")
        self.assertTrue(a[ns("exif", "GPSLongitude")].endswith("E"))

    def test_raw_wins_name_clash_and_no_overwrite(self):
        self.run_tool()
        with open(self.report, encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("skipped_name_clash", report)
        self.assertIn("IMG_0001.JPG", report)
        # Second run must not clobber.
        self.run_tool()
        with open(self.report, encoding="utf-8") as fh:
            self.assertIn("skipped_xmp_exists", fh.read())

    def test_dry_run_writes_nothing(self):
        self.run_tool("--dry-run")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "photos", "2014", "IMG_0001.xmp")))

    def test_remap(self):
        moved = os.path.join(self.tmp.name, "moved")
        os.rename(os.path.join(self.tmp.name, "photos"), moved)
        old = os.path.join(self.tmp.name, "photos").replace("\\", "/")
        self.run_tool("--remap", f"{old}={moved}")
        self.assertTrue(os.path.exists(os.path.join(moved, "2014", "IMG_0001.xmp")))

    def test_auto_find_moved_folder(self):
        # Photos copied onto an "external drive" folder: ext/photos/2014/...
        ext = os.path.join(self.tmp.name, "ext")
        os.makedirs(ext)
        os.rename(os.path.join(self.tmp.name, "photos"), os.path.join(ext, "photos"))
        self.run_tool("--photos-dir", ext)
        self.assertTrue(os.path.exists(os.path.join(ext, "photos", "2014", "IMG_0001.xmp")))

    def test_auto_find_when_root_contents_moved_to_drive_top(self):
        # Contents of the old root now sit directly in the chosen folder: ext/2014/...
        ext = os.path.join(self.tmp.name, "ext")
        os.rename(os.path.join(self.tmp.name, "photos"), ext)
        self.run_tool("--photos-dir", ext)
        self.assertTrue(os.path.exists(os.path.join(ext, "2014", "IMG_0001.xmp")))

    def test_nothing_found_reports_missing(self):
        os.rename(os.path.join(self.tmp.name, "photos"), os.path.join(self.tmp.name, "gone"))
        self.run_tool("--no-auto-find")
        with open(self.report, encoding="utf-8") as fh:
            self.assertIn("photo_not_found", fh.read())

    def test_catalog_untouched(self):
        before = os.path.getmtime(self.cat), os.path.getsize(self.cat)
        self.run_tool()
        self.assertEqual(before, (os.path.getmtime(self.cat), os.path.getsize(self.cat)))


class Paths(unittest.TestCase):
    def test_path_parts(self):
        self.assertEqual(lrrecover._path_parts("D:/Photos/2014/"), ["Photos", "2014"])
        self.assertEqual(lrrecover._path_parts("\\\\nas\\share\\Pics\\"), ["Pics"])
        self.assertEqual(lrrecover._path_parts("/Volumes/Disk/Pics/"), ["Volumes", "Disk", "Pics"])

    def test_windows_catalog_on_mac_volume(self):
        # Catalog made on Windows (E:\Photos), drive now mounted on a Mac.
        cands = list(lrrecover._candidate_roots("E:/Photos/2014/", ["/Volumes/PhotoDrive"]))
        self.assertEqual(cands[0], "/Volumes/PhotoDrive/Photos/2014/")
        # Mac catalog, drive renamed.
        cands = list(lrrecover._candidate_roots("/Volumes/OldName/Pics/", ["/Volumes/NewName"]))
        self.assertIn("/Volumes/NewName/Pics/", cands)

    def test_other_drive_letter_is_tried(self):
        cands = list(lrrecover._candidate_roots("Q:/Photos/Old/", []))
        drive = lrrecover.available_drives()[0]
        self.assertIn(drive + "Photos/Old/", cands)
        self.assertIn(drive + "Old/", cands)


class LuaParser(unittest.TestCase):
    def test_nested_and_keys(self):
        d = lrrecover.parse_lua_settings('s = { A = -1.5e2, ["B c"] = "x\\ny", T = { 1, 2 }, E = {}, }')
        self.assertEqual(d["A"], -150.0)
        self.assertEqual(d["B c"], "x\ny")
        self.assertEqual(d["T"], [1, 2])


if __name__ == "__main__":
    unittest.main()
