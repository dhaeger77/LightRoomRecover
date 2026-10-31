"""Finding photos when only some of them were copied (a zipped sample set, or a
library split across two drives).
Run:  python -m unittest discover tests
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lrrecover  # noqa: E402

GONE_ROOT = "Q:/NoSuchDrive/Photos/"   # where the catalog thinks the photos are


def make_catalog(path, root, files):
    """files: [(folder_from_root, base, ext)]"""
    con = sqlite3.connect(path)
    con.executescript('''
    CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY, absolutePath);
    CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY, rootFolder, pathFromRoot);
    CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, folder, baseName, extension);
    CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rootFile, masterImage, rating);
    ''')
    con.execute("INSERT INTO AgLibraryRootFolder VALUES (1, ?)", (root,))
    folders = {}
    for i, (folder, base, ext) in enumerate(files, 1):
        if folder not in folders:
            folders[folder] = len(folders) + 1
            con.execute("INSERT INTO AgLibraryFolder VALUES (?, 1, ?)", (folders[folder], folder))
        con.execute("INSERT INTO AgLibraryFile VALUES (?, ?, ?, ?)", (i, folders[folder], base, ext))
        con.execute("INSERT INTO Adobe_images VALUES (?, ?, NULL, 3)", (i, i))
    con.commit()
    con.close()


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").close()


class PartialFind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name.replace("\\", "/")
        self.cat = os.path.join(self.d, "t.lrcat")
        self.report = os.path.join(self.d, "r.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *search):
        return lrrecover.run(lrrecover.Options(catalog=self.cat, report=self.report,
                                               search_dirs=list(search)), log=lambda m: None)

    def test_zipped_sample_set(self):
        # 20 photos in the catalog; the zip only holds 2 of them, same folders.
        files = [("2005/2005-06-01/", f"IMG_{i:04d}", "CR2") for i in range(20)]
        make_catalog(self.cat, GONE_ROOT, files)
        unzipped = f"{self.d}/sample"
        for i in (3, 11):
            touch(f"{unzipped}/Photos/2005/2005-06-01/IMG_{i:04d}.CR2")
        res = self.run_tool(unzipped)
        self.assertEqual(len(res.moves), 1)
        old, new, n, total = res.moves[0]
        self.assertEqual((n, total), (2, 20))
        self.assertTrue(new.endswith("/sample/Photos/"))
        self.assertEqual(res.written, 2)
        self.assertEqual(res.missing_photo, 18)
        self.assertTrue(os.path.exists(f"{unzipped}/Photos/2005/2005-06-01/IMG_0003.xmp"))

    def test_sample_zipped_from_inside_the_root(self):
        # Zip starts at the year folder: sample/2005/2005-06-01/...
        files = [("2005/2005-06-01/", f"IMG_{i:04d}", "CR2") for i in range(20)]
        make_catalog(self.cat, GONE_ROOT, files)
        unzipped = f"{self.d}/sample"
        touch(f"{unzipped}/2005/2005-06-01/IMG_0005.CR2")
        res = self.run_tool(unzipped)
        self.assertEqual(res.written, 1)

    def test_lone_file_with_no_folders_is_not_a_match(self):
        files = [("", f"IMG_{i:04d}", "CR2") for i in range(20)]
        make_catalog(self.cat, GONE_ROOT, files)
        touch(f"{self.d}/elsewhere/IMG_0001.CR2")
        res = self.run_tool(f"{self.d}/elsewhere")
        self.assertEqual(res.moves, [])
        self.assertEqual(res.written, 0)

    def test_photos_still_in_place_are_left_alone(self):
        root = f"{self.d}/orig/"
        files = [("2005/", f"IMG_{i:04d}", "CR2") for i in range(10)]
        make_catalog(self.cat, root, files)
        for i in range(10):
            # 0-3 still in the original place, 4-9 moved to the new drive
            touch(f"{self.d}/orig/2005/IMG_{i:04d}.CR2" if i < 4
                  else f"{self.d}/newdrive/orig/2005/IMG_{i:04d}.CR2")
        res = self.run_tool(f"{self.d}/newdrive")
        self.assertEqual(res.written, 10)
        self.assertTrue(os.path.exists(f"{self.d}/orig/2005/IMG_0001.xmp"))
        self.assertTrue(os.path.exists(f"{self.d}/newdrive/orig/2005/IMG_0007.xmp"))


if __name__ == "__main__":
    unittest.main()
