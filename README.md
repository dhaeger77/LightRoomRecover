# lrrecover

Gets photo edits out of an old Adobe Lightroom catalog (`.lrcat`) and saves them
as small `.xmp` files next to each photo, which darktable can read.

The catalog is never changed. The tool works on a temporary copy.

## Easy way: LightroomRecover.exe

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

Same window and steps as on Windows. Works on Apple Silicon Macs.

1. Unzip `LightroomRecover-mac.zip` and drag **Lightroom Recover** to Applications.
2. The first time, macOS blocks it because it isn't from an identified developer.
   Double-click it once (click **Done**), then open **System Settings → Privacy & Security**,
   scroll down and click **Open Anyway**.
3. When macOS asks to let it access files on a removable volume, click **Allow**.
   That's needed to read the photos on the external drive.

External drives show up under `/Volumes`. The tool finds the photos there even if
the catalog was made on Windows (`E:\Photos` → `/Volumes/<drive name>/Photos`).

Build it on a Mac with `./build_mac.sh`, or run the GitHub Actions workflow in
`.github/workflows/build.yml`, which builds both the Mac and Windows versions.

## Command line

Needs Python 3.8 or newer. Nothing else to install.

```
# 1. See what would happen (writes nothing except a report)
python lrrecover.py "D:\Lightroom\Lightroom 5 Catalog.lrcat" --dry-run

# 2. Write the sidecars
python lrrecover.py "D:\Lightroom\Lightroom 5 Catalog.lrcat"
```

Photos that moved (new drive letter, copied into another folder) are found
automatically. If not, point it at where they are now:

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

## Tests and building the exe

```
python -m unittest discover tests

python -m venv .venv
.venv\Scripts\pip install pyinstaller==6.11.1
.venv\Scripts\pyinstaller --onefile --windowed --name LightroomRecover --distpath dist --workpath build --specpath build lrrecover_gui.py
```
