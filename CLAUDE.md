# LightRoomRecover

Tool for the user's brother: recover photo edits from an old Adobe Lightroom catalog
(`.lrcat`) and move them to darktable. His photos live on a local external drive,
so everything runs on his own computer (an Unraid/server version was considered and dropped).

## How it works
- `lrrecover.py` — core + CLI, Python stdlib only. Copies the catalog to a temp dir
  (never modifies the original), reads it with sqlite3, writes Lightroom-style XMP
  sidecars `IMG_1234.xmp` next to `IMG_1234.CR2`. darktable's built-in Lightroom
  importer (`src/develop/lightroom.c`) reads that file name pattern, not `IMG_1234.CR2.xmp`.
  - Develop settings come from `Adobe_imageDevelopSettings.text` (a Lua table; small
    parser in the file). All crs: values are written, even ones darktable ignores.
  - Orientation/dimensions/title come from `Adobe_AdditionalMetadata.xmp`
    (plain or zlib with 4-byte length prefix).
  - Lists must stay indented: darktable walks list children through whitespace text nodes.
  - Must include `stEvt:softwareAgent` containing "Lightroom" (darktable checks it).
  - Keywords → `lr:hierarchicalSubject`; collections → `Lightroom|collections|...` tags;
    pick/reject and virtual copies → tags. RAW wins when RAW+JPG share a base name.
  - Finds moved photos: tries the catalog's root folders on every drive / `/Volumes`
    and with leading folders dropped, confirmed by sampling files (`find_moved_roots`).
- `smartcollections.py` — decodes smart collection rules (Lua in AgLibraryCollectionContent,
  owningModule 'ag.library.smart_collection') and evaluates them per photo in Python.
  Matching photos get `Lightroom|smart collections|...` tags; any unsupported rule means
  the collection is only listed (in `<report>-smart-collections.txt`), never mis-tagged.
  Format details were learned from fdenivac/Lightroom-SQL-tools (GPL — don't copy code).
  Unverified on a real catalog: `_units` key for inLast, labelColor numbers, shutter direction.
- Preview logs an organization summary (ratings, labels, keywords, collections, smart collections).
- The brother's priority is ratings, keywords and organization, not edit looks.
  Never commit his catalog/photos (public repo); `samples/` is gitignored.
- `lrrecover_gui.py` — tkinter window (Preview / Write / Open report).
  Packaged with PyInstaller as `LightroomRecover.exe` and `Lightroom Recover.app`.
- `.github/workflows/build.yml` builds Windows + one universal Mac app (python.org universal2 Python + PyInstaller --target-arch universal2), then launches it with `--self-test` on both an arm64 and an Intel runner. The user's own MacBook Pro is Intel (macOS 26). Version tags publish a Release. `build_mac.sh` builds locally on a Mac.

## Status (2026-10-31)
- 26 unit tests pass on Windows and macOS (CI). Tests use a fake catalog built in
  `tests/test_lrrecover.py`; schema there is a subset of the real one.
- NOT yet tested: a real Lightroom catalog, the Mac app's window on a real Mac,
  importing the sidecars into real darktable.

## Next steps
1. darktable Lua add-on to apply what darktable's importer ignores: white balance
   (Temperature/Tint), Highlights2012/Shadows2012/Whites2012, Contrast2012, Vibrance,
   Saturation, sharpening, noise reduction. Values are already in the sidecars.
   Approximate mappings; needs darktable installed to develop/test.
2. Test on the brother's real `.lrcat` (only the .lrcat is needed, not the .lrdata
   preview folders) plus a few heavily edited RAW files.

## Commands
- Tests: `python -m unittest discover tests`
- Mac build: `./build_mac.sh` (needs python.org Python 3.12 for Tk)
