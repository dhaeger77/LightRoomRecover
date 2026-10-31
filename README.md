# Lightroom Recover

Gets photo edits out of an old Adobe Lightroom catalog (`.lrcat`) and saves them
as small `.xmp` files next to each photo, which [darktable](https://www.darktable.org/)
(free, Windows/Mac/Linux) can read.

The catalog is never changed. The tool works on a temporary copy.

**[Download the latest version](https://github.com/dhaeger77/LightRoomRecover/releases/latest)**

## Why this tool?

Lightroom keeps your edits inside its catalog, not next to your photos. darktable
can import Lightroom edits, but only from `.xmp` files beside each photo, which
Lightroom doesn't write unless that setting was turned on. Without them, darktable
shows your RAW files unedited.

This tool reads the catalog directly and writes those files. No Lightroom or
Adobe account needed. It also:

- turns your **collections** into darktable tags (Lightroom's own export leaves them out)
- finds photos on a drive whose letter or folder has changed

**Already have `.xmp` files next to your photos?** Then that setting was on, and
darktable can read most of your edits without this tool.

**Still have Lightroom (or its free trial)?** Select all photos, then
**Metadata → Save Metadata to Files** (Ctrl+S / Cmd+S) does much the same job,
minus the collections. While you're in there, export full-quality copies of your
edited photos too. That's the only way to keep them looking exactly as you left them.

### Other apps

| App | Cost | Brings over Lightroom edits? |
|---|---|---|
| [darktable](https://www.darktable.org/) | Free | Partly, from `.xmp` files: crop, exposure, tone curve, color, spot removal, ratings, keywords |
| [Capture One](https://www.captureone.com/) | Paid | Partly, has a built-in Lightroom catalog importer |
| [ON1 Photo RAW](https://www.on1.com/products/photo-raw/) | Paid | Partly, has a Lightroom migration tool |
| [Adobe Lightroom Classic](https://www.adobe.com/products/photoshop-lightroom-classic.html) | Subscription | Yes, everything |
| [RawTherapee](https://www.rawtherapee.com/) / [ART](https://art.pixls.us/) | Free | No, you start over |
| [digiKam](https://www.digikam.org/) | Free | Ratings and keywords only (photo organizer, not an editor) |

No app other than Lightroom reproduces Adobe's edits exactly. Expect "close", not identical.

## Windows: LightroomRecover.exe

1. Close Lightroom. Plug in the drive with the photos.
2. Double-click `LightroomRecover.exe`.
   Windows may say "Windows protected your PC" because the program isn't signed:
   click **More info**, then **Run anyway**.
3. **Step 1:** choose the catalog (`.lrcat`, usually in `Pictures\Lightroom`).
4. **Step 2:** leave it empty. The tool searches every drive for the photos, even if
   the drive letter changed. If it can't find them, choose the photo folder here.
5. Click **Preview** and check the numbers, then **Write sidecar files**.
   If the photos moved, it asks before using the new location.

A report of every photo is saved in `Documents` (button **Open report**).

## Mac: Lightroom Recover.app

Same window and steps as on Windows. Works on both Apple Silicon and Intel Macs.

1. Unzip `LightroomRecover-mac.zip` and drag **Lightroom Recover** to Applications.
2. The first time, macOS blocks it because it isn't from an identified developer.
   Double-click it once (click **Done**), then open **System Settings → Privacy & Security**,
   scroll down and click **Open Anyway**.
3. When macOS asks to let it access files on a removable volume, click **Allow**.
   That's needed to read the photos on the external drive.
4. Follow steps 3–5 from the Windows section.

The tool finds the photos on the external drive even if the catalog was made on Windows.

## Then in darktable

Import the photo folders. darktable finds `IMG_1234.xmp` next to `IMG_1234.CR2`
and brings in the Lightroom edits. Imported steps are labeled "lr import" in each
photo's history.

## What carries over

- **Edits:** crop and straighten, exposure, blacks, tone curve (point and parametric),
  HSL color adjustments, split toning, clarity, post-crop vignette, grain, spot removal
- **Organization:** star ratings, color labels, keywords (with hierarchy),
  collections (as `Lightroom|collections|...` tags), pick/reject flags (as tags),
  title, caption, copyright, GPS

**Not carried over by darktable's importer:** white balance, highlights/shadows/whites,
contrast, vibrance/saturation, sharpening, noise reduction, lens corrections,
graduated/radial filters and brushes, and the newer Lightroom masking.
Virtual copies are not exported (the photo is tagged `Lightroom|had virtual copies`).

Results will look close to Lightroom but not identical, because darktable
processes images differently from Adobe.

---

## For developers

### Getting the builds

Finished versions are on the repo's **Releases** page.

To publish a new one, bump `VERSION` in `lrrecover.py`, then push a tag:

```
git tag -a v0.2.0 -m "..." && git push origin v0.2.0
```

GitHub Actions (`.github/workflows/build.yml`) builds both versions and creates the
release. For a test build without a release, use **Actions** → **build** →
**Run workflow** and download the files under the run's Artifacts.

### Building locally

Mac (needs Python 3.12 from python.org: it includes Tk and is universal, so the app runs on Apple Silicon and Intel):

```
./build_mac.sh        # -> dist/LightroomRecover-mac.zip (universal with python.org Python)
```

Windows:

```
python -m venv .venv
.venv\Scripts\pip install pyinstaller==6.11.1
.venv\Scripts\pyinstaller --onefile --windowed --name LightroomRecover --distpath dist --workpath build --specpath build lrrecover_gui.py
```

### Tests

```
python -m unittest discover tests
```

### Command line

`lrrecover.py` runs without the window. Needs Python 3.8 or newer, nothing else.

```
# 1. See what would happen (writes nothing except a report)
python lrrecover.py "D:\Lightroom\Lightroom 5 Catalog.lrcat" --dry-run

# 2. Write the sidecars
python lrrecover.py "D:\Lightroom\Lightroom 5 Catalog.lrcat"
```

Photos that moved (new drive letter, copied into another folder, or a Windows
catalog read on a Mac where drives are under `/Volumes`) are found automatically.
If not, point it at where they are now:

```
python lrrecover.py CATALOG --photos-dir "F:/"
python lrrecover.py CATALOG --remap "D:/Photos=E:/Old Drive/Photos"   # exact mapping
```

`lrrecover-report.csv` lists every photo and what happened to it.

| Option | What it does |
|---|---|
| `--dry-run` | Report only, write nothing |
| `--photos-dir DIR` | Extra place to look for photos that moved (can repeat) |
| `--remap OLD=NEW` | Exact folder fix for photos that moved (can repeat) |
| `--no-auto-find` | Don't search other drives |
| `--yes` | Accept found locations without asking |
| `--out-dir DIR` | Put sidecars in a separate folder instead of next to the photos |
| `--overwrite` | Replace existing `.xmp` files (keeps the old one as `.xmp.bak`) |
| `--edited-only` | Skip photos that have no develop edits |
| `--no-collections` | Don't turn collections into tags |
