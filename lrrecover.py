#!/usr/bin/env python3
"""
lrrecover - pull photo edits out of an Adobe Lightroom catalog (.lrcat) and
write them as Lightroom-style XMP sidecars (IMG_1234.xmp next to IMG_1234.CR2)
so darktable can import them.

The catalog is never modified: it is copied to a temp folder and read there.

Usage:
    python lrrecover.py "D:/Lightroom/My Catalog.lrcat" --dry-run
    python lrrecover.py "D:/Lightroom/My Catalog.lrcat"
    python lrrecover.py CATALOG --remap "D:/Photos=E:/Backup/Photos"
    python lrrecover.py CATALOG --out-dir "E:/sidecars"

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

VERSION = "0.1.1"

NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "crs": "http://ns.adobe.com/camera-raw-settings/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "tiff": "http://ns.adobe.com/tiff/1.0/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
    "exif": "http://ns.adobe.com/exif/1.0/",
    "lr": "http://ns.adobe.com/lightroom/1.0/",
    "xmpMM": "http://ns.adobe.com/xap/1.0/mm/",
    "stEvt": "http://ns.adobe.com/xap/1.0/sType/ResourceEvent#",
}

# Lightroom treats these as "the original" when a RAW and a JPEG share a name.
RAW_PRIORITY = {"RAW": 0, "DNG": 1}

# Fallback when the catalog's stored XMP has no tiff:Orientation.
# Lightroom stores orientation as the two original corners now at the
# top-left / top-right (A=TL, B=TR, C=BR, D=BL).
LR_ORIENTATION_TO_EXIF = {
    "AB": 1, "BA": 2, "CD": 3, "DC": 4,
    "AD": 5, "DA": 6, "CB": 7, "BC": 8,
}


# ---------------------------------------------------------------------------
# Lua table parser (Adobe_imageDevelopSettings.text looks like "s = { ... }")
# ---------------------------------------------------------------------------

class LuaParseError(ValueError):
    pass


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+|--\[\[.*?\]\]|--[^\n]*)
  | (?P<longstr>\[(?P<eq>=*)\[.*?\](?P=eq)\])
  | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<num>-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?))
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<sym>[{}\[\]=,;])
    """,
    re.VERBOSE | re.DOTALL,
)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'",
            "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\n": "\n"}


def _unescape_lua(body: str) -> str:
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= len(body):
            break
        c = body[i]
        if c.isdigit():
            m = re.match(r"\d{1,3}", body[i:])
            out.append(chr(int(m.group())))
            i += len(m.group())
        else:
            out.append(_ESCAPES.get(c, c))
            i += 1
    return "".join(out)


def _tokenize(text: str):
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise LuaParseError(f"unexpected character {text[pos]!r} at {pos}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "eq":  # inner group of longstr
            kind = "longstr"
        if kind == "ws":
            continue
        val = m.group(kind) if kind != "longstr" else m.group("longstr")
        if kind == "longstr":
            eq = m.group("eq")
            inner = val[len(eq) + 2: -(len(eq) + 2)]
            if inner.startswith("\n"):
                inner = inner[1:]
            yield ("str", inner)
        elif kind == "str":
            yield ("str", _unescape_lua(val[1:-1]))
        elif kind == "num":
            if val.lower().startswith(("0x", "-0x")):
                yield ("num", int(val, 16))
            elif re.fullmatch(r"-?\d+", val):
                yield ("num", int(val))
            else:
                yield ("num", float(val))
        elif kind == "name":
            if val == "true":
                yield ("bool", True)
            elif val == "false":
                yield ("bool", False)
            elif val == "nil":
                yield ("nil", None)
            else:
                yield ("name", val)
        else:
            yield ("sym", val)
    yield ("eof", None)


class _LuaParser:
    def __init__(self, text: str):
        self.toks = list(_tokenize(text))
        self.i = 0

    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def take(self, kind=None, val=None):
        tok = self.peek()
        if kind and tok[0] != kind or val is not None and tok[1] != val:
            raise LuaParseError(f"expected {kind} {val!r}, got {tok!r}")
        self.i += 1
        return tok

    def value(self):
        kind, val = self.peek()
        if kind == "sym" and val == "{":
            return self.table()
        if kind in ("str", "num", "bool", "nil"):
            self.i += 1
            return val
        raise LuaParseError(f"unexpected token {self.peek()!r}")

    def table(self):
        self.take("sym", "{")
        items: dict = {}
        arr: list = []
        while not (self.peek() == ("sym", "}")):
            k0, v0 = self.peek()
            if k0 == "name" and self.peek(1) == ("sym", "="):
                self.i += 2
                items[v0] = self.value()
            elif k0 == "sym" and v0 == "[":
                self.i += 1
                key = self.value()
                self.take("sym", "]")
                self.take("sym", "=")
                items[key] = self.value()
            else:
                arr.append(self.value())
            if self.peek()[0] == "sym" and self.peek()[1] in (",", ";"):
                self.i += 1
        self.take("sym", "}")
        if arr and not items:
            return arr
        if arr:
            for n, v in enumerate(arr, 1):
                items.setdefault(n, v)
        return items


def parse_lua_settings(text: str) -> dict:
    """Parse 's = { Key = value, ... }' into a dict."""
    if not text:
        return {}
    p = _LuaParser(text)
    if p.peek()[0] == "name" and p.peek(1) == ("sym", "="):
        p.i += 2
    result = p.value()
    if isinstance(result, list):  # empty table parses as []
        return {} if not result else {i + 1: v for i, v in enumerate(result)}
    return result


# ---------------------------------------------------------------------------
# Catalog reading
# ---------------------------------------------------------------------------

@dataclass
class Photo:
    image_id: int
    path: str
    file_format: str
    root: str = ""   # catalog's root folder for this photo, e.g. "D:/Photos/"
    rel: str = ""    # path below root, e.g. "2014/IMG_0001.CR2"
    rating: int = 0
    label: str = ""
    pick: int = 0
    orientation: str = ""
    width: int | None = None
    height: int | None = None
    develop: dict = field(default_factory=dict)
    develop_error: str = ""
    stored_xmp: ET.Element | None = None
    keywords: list[str] = field(default_factory=list)
    collections: list[str] = field(default_factory=list)
    gps: tuple[float, float] | None = None
    caption: str = ""
    copyright: str = ""
    title: str = ""
    virtual_copies: int = 0


def open_catalog_copy(catalog: str, workdir: str) -> sqlite3.Connection:
    """Copy the catalog (and any -wal journal) to workdir and open the copy."""
    dst = os.path.join(workdir, "catalog.lrcat")
    shutil.copy2(catalog, dst)
    if os.path.exists(catalog + "-wal"):
        shutil.copy2(catalog + "-wal", dst + "-wal")
    con = sqlite3.connect(dst)
    con.row_factory = sqlite3.Row
    return con


def _columns(con, table) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def _tables(con) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _decode_stored_xmp(blob) -> ET.Element | None:
    """Adobe_AdditionalMetadata.xmp is plain text in older catalogs and
    zlib-compressed (with a 4-byte length prefix) in newer ones."""
    if blob is None:
        return None
    data = blob.encode("utf-8") if isinstance(blob, str) else bytes(blob)
    if not data.lstrip().startswith(b"<"):
        for skip in (4, 0):
            try:
                data = zlib.decompress(data[skip:])
                break
            except zlib.error:
                continue
        else:
            return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _keyword_paths(con) -> dict[int, str]:
    rows = {r["id_local"]: (r["name"], r["parent"]) for r in
            con.execute("SELECT id_local, name, parent FROM AgLibraryKeyword")}
    cache: dict[int, str] = {}

    def path(kid, depth=0):
        if kid in cache:
            return cache[kid]
        name, parent = rows.get(kid, (None, None))
        if name is None or depth > 50:
            return ""
        parent_path = path(parent, depth + 1) if parent in rows else ""
        cache[kid] = f"{parent_path}|{name}" if parent_path else name
        return cache[kid]

    return {kid: path(kid) for kid in rows}


def _collection_paths(con) -> dict[int, str]:
    rows = {r["id_local"]: (r["name"], r["parent"]) for r in
            con.execute("SELECT id_local, name, parent FROM AgLibraryCollection")}

    def path(cid, depth=0):
        name, parent = rows.get(cid, (None, None))
        if not name or depth > 50:
            return ""
        pp = path(parent, depth + 1) if parent in rows else ""
        return f"{pp}|{name}" if pp else name

    return {cid: path(cid) for cid in rows}


def read_catalog(con: sqlite3.Connection) -> tuple[list[Photo], list[str]]:
    warnings: list[str] = []
    tables = _tables(con)
    needed = {"Adobe_images", "AgLibraryFile", "AgLibraryFolder", "AgLibraryRootFolder"}
    missing = needed - tables
    if missing:
        raise SystemExit(f"This does not look like a Lightroom catalog (missing {', '.join(sorted(missing))}).")

    img_cols = _columns(con, "Adobe_images")
    sel = ["i.id_local AS id", "i.masterImage AS master",
           "rf.absolutePath AS root", "fo.pathFromRoot AS folder",
           "f.baseName AS base", "f.extension AS ext"]
    for col, alias in [("fileFormat", "fmt"), ("rating", "rating"), ("colorLabels", "label"),
                       ("pick", "pick"), ("orientation", "orient"),
                       ("fileWidth", "w"), ("fileHeight", "h")]:
        sel.append(f"i.{col} AS {alias}" if col in img_cols else f"NULL AS {alias}")

    query = f"""
        SELECT {', '.join(sel)}
        FROM Adobe_images i
        JOIN AgLibraryFile f ON f.id_local = i.rootFile
        JOIN AgLibraryFolder fo ON fo.id_local = f.folder
        JOIN AgLibraryRootFolder rf ON rf.id_local = fo.rootFolder
    """
    photos: dict[int, Photo] = {}
    vc_count: dict[int, int] = {}
    for r in con.execute(query):
        if r["master"]:
            vc_count[r["master"]] = vc_count.get(r["master"], 0) + 1
            continue
        root = (r["root"] or "").replace("\\", "/")
        rel = (r["folder"] or "") + (r["base"] or "")
        if r["ext"]:
            rel += "." + r["ext"]
        p = Photo(
            image_id=r["id"], path=os.path.normpath(root + rel), root=root, rel=rel,
            file_format=(r["fmt"] or "").upper(),
            rating=int(r["rating"] or 0), label=r["label"] or "",
            pick=int(r["pick"] or 0), orientation=r["orient"] or "",
            width=int(r["w"]) if r["w"] else None,
            height=int(r["h"]) if r["h"] else None,
        )
        photos[p.image_id] = p
    for mid, n in vc_count.items():
        if mid in photos:
            photos[mid].virtual_copies = n

    if "Adobe_imageDevelopSettings" in tables:
        for r in con.execute("SELECT image, text FROM Adobe_imageDevelopSettings"):
            p = photos.get(r["image"])
            if not p or not r["text"]:
                continue
            try:
                p.develop = parse_lua_settings(r["text"])
            except (LuaParseError, RecursionError) as e:
                p.develop_error = str(e)

    if "Adobe_AdditionalMetadata" in tables and "xmp" in _columns(con, "Adobe_AdditionalMetadata"):
        for r in con.execute("SELECT image, xmp FROM Adobe_AdditionalMetadata"):
            p = photos.get(r["image"])
            if p:
                p.stored_xmp = _decode_stored_xmp(r["xmp"])

    if {"AgLibraryKeyword", "AgLibraryKeywordImage"} <= tables:
        kpaths = _keyword_paths(con)
        for r in con.execute("SELECT image, tag FROM AgLibraryKeywordImage"):
            p = photos.get(r["image"])
            kp = kpaths.get(r["tag"])
            if p and kp and kp not in p.keywords:
                p.keywords.append(kp)

    if {"AgLibraryCollection", "AgLibraryCollectionImage"} <= tables:
        cpaths = _collection_paths(con)
        coll_cols = _columns(con, "AgLibraryCollection")
        skip = set()
        if "creationId" in coll_cols:
            # Skip publish-service and quick collections, keep user collections.
            for r in con.execute("SELECT id_local, creationId FROM AgLibraryCollection"):
                cid = r["creationId"] or ""
                if cid and not cid.startswith("com.adobe.ag.library.collection"):
                    skip.add(r["id_local"])
        for r in con.execute("SELECT image, collection FROM AgLibraryCollectionImage"):
            p = photos.get(r["image"])
            cp = cpaths.get(r["collection"])
            if p and cp and r["collection"] not in skip and cp not in p.collections:
                p.collections.append(cp)

    exif_cols = _columns(con, "AgHarvestedExifMetadata")
    if {"gpsLatitude", "gpsLongitude"} <= exif_cols:
        for r in con.execute("SELECT image, gpsLatitude, gpsLongitude FROM AgHarvestedExifMetadata "
                             "WHERE gpsLatitude IS NOT NULL AND gpsLongitude IS NOT NULL"):
            p = photos.get(r["image"])
            if p:
                p.gps = (float(r["gpsLatitude"]), float(r["gpsLongitude"]))

    iptc_cols = _columns(con, "AgLibraryIPTC")
    if "image" in iptc_cols:
        want = [c for c in ("caption", "copyright") if c in iptc_cols]
        if want:
            for r in con.execute(f"SELECT image, {', '.join(want)} FROM AgLibraryIPTC"):
                p = photos.get(r["image"])
                if p:
                    p.caption = (r["caption"] if "caption" in want else "") or ""
                    p.copyright = (r["copyright"] if "copyright" in want else "") or ""

    return list(photos.values()), warnings


# ---------------------------------------------------------------------------
# XMP writing
# ---------------------------------------------------------------------------

def _fmt_num(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return f"{v:.6f}".rstrip("0").rstrip(".")
    return str(v)


def _stored(root: ET.Element | None, ns: str, name: str) -> str | None:
    """Find a scalar (attribute or element) in the catalog's stored XMP."""
    if root is None:
        return None
    key = f"{{{NS[ns]}}}{name}"
    for desc in root.iter(f"{{{NS['rdf']}}}Description"):
        if key in desc.attrib:
            return desc.attrib[key]
        el = desc.find(key)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return None


def _stored_list(root: ET.Element | None, ns: str, name: str) -> list[str]:
    if root is None:
        return []
    el = root.find(f".//{{{NS[ns]}}}{name}")
    if el is None:
        return []
    return [li.text for li in el.iter(f"{{{NS['rdf']}}}li") if li.text]


def _gps_string(value: float, pos: str, neg: str) -> str:
    """Decimal degrees -> XMP GPSCoordinate 'DDD,MM.mmmmmmK'."""
    ref = pos if value >= 0 else neg
    value = abs(value)
    deg = int(value)
    minutes = (value - deg) * 60
    return f"{deg},{minutes:.6f}{ref}"


SKIPPED_COMPLEX = "skipped_complex"


def build_develop_attrs(dev: dict) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """Turn Lightroom develop settings into crs: attributes and list properties.
    Returns (scalars, lists, skipped_keys)."""
    scalars: dict[str, str] = {}
    lists: dict[str, list[str]] = {}
    skipped: list[str] = []
    for key, val in dev.items():
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        if isinstance(val, (bool, int, float)):
            scalars[key] = _fmt_num(val)
        elif isinstance(val, str):
            scalars[key] = val
        elif isinstance(val, list):
            if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val):
                if key.startswith("ToneCurve") and len(val) % 2 == 0:
                    lists[key] = [f"{_fmt_num(val[i])}, {_fmt_num(val[i + 1])}"
                                  for i in range(0, len(val), 2)]
                else:
                    lists[key] = [_fmt_num(x) for x in val]
            elif all(isinstance(x, str) for x in val):
                lists[key] = list(val)
            else:
                skipped.append(key)
        elif isinstance(val, dict) and not val:
            continue
        else:
            skipped.append(key)

    # darktable only reads the 2012-era names. Older process versions
    # (Lightroom 3 and earlier edits) use the plain names, so mirror them.
    if "Exposure2012" not in scalars and "Exposure" in scalars:
        scalars["Exposure2012"] = scalars["Exposure"]
    if "Clarity2012" not in scalars and "Clarity" in scalars:
        scalars["Clarity2012"] = scalars["Clarity"]
    if "ToneCurvePV2012" not in lists and "ToneCurve" in lists:
        lists["ToneCurvePV2012"] = lists["ToneCurve"]
    return scalars, lists, skipped


def build_xmp(p: Photo, include_collections: bool = True) -> tuple[str, list[str]]:
    notes: list[str] = []
    attrs: list[tuple[str, str]] = []
    children: list[str] = []

    def li_block(prop: str, kind: str, items: list[str], lang: bool = False) -> str:
        lis = "\n".join(
            f"     <rdf:li{' xml:lang=\"x-default\"' if lang else ''}>{escape(i)}</rdf:li>"
            for i in items)
        return f"   <{prop}>\n    <rdf:{kind}>\n{lis}\n    </rdf:{kind}>\n   </{prop}>"

    # --- library metadata --------------------------------------------------
    if p.rating:
        attrs.append(("xmp:Rating", str(p.rating)))
    if p.label:
        attrs.append(("xmp:Label", p.label))

    orient = _stored(p.stored_xmp, "tiff", "Orientation")
    if not orient and p.orientation in LR_ORIENTATION_TO_EXIF:
        orient = str(LR_ORIENTATION_TO_EXIF[p.orientation])
    if orient:
        attrs.append(("tiff:Orientation", orient))
    w = _stored(p.stored_xmp, "tiff", "ImageWidth") or (str(p.width) if p.width else None)
    h = _stored(p.stored_xmp, "tiff", "ImageLength") or (str(p.height) if p.height else None)
    if w and h:
        attrs.append(("tiff:ImageWidth", w))
        attrs.append(("tiff:ImageLength", h))

    lat = _stored(p.stored_xmp, "exif", "GPSLatitude")
    lon = _stored(p.stored_xmp, "exif", "GPSLongitude")
    if not (lat and lon) and p.gps:
        lat = _gps_string(p.gps[0], "N", "S")
        lon = _gps_string(p.gps[1], "E", "W")
    if lat and lon:
        attrs.append(("exif:GPSLatitude", lat))
        attrs.append(("exif:GPSLongitude", lon))

    # --- develop settings ----------------------------------------------------
    scalars, lists, skipped = build_develop_attrs(p.develop)
    for k in sorted(scalars):
        attrs.append((f"crs:{k}", scalars[k]))
    for k in sorted(lists):
        children.append(li_block(f"crs:{k}", "Seq", lists[k]))
    if skipped:
        notes.append("not converted: " + ", ".join(sorted(skipped)))
    if p.develop_error:
        notes.append("could not read develop settings: " + p.develop_error)

    # --- text metadata -------------------------------------------------------
    title = _stored_list(p.stored_xmp, "dc", "title")
    desc = _stored_list(p.stored_xmp, "dc", "description") or ([p.caption] if p.caption else [])
    creator = _stored_list(p.stored_xmp, "dc", "creator")
    rights = _stored_list(p.stored_xmp, "dc", "rights") or ([p.copyright] if p.copyright else [])
    if title:
        children.append(li_block("dc:title", "Alt", title[:1], lang=True))
    if desc:
        children.append(li_block("dc:description", "Alt", desc[:1], lang=True))
    if creator:
        children.append(li_block("dc:creator", "Seq", creator))
    if rights:
        children.append(li_block("dc:rights", "Alt", rights[:1], lang=True))

    tags = list(p.keywords)
    if include_collections:
        tags += [f"Lightroom|collections|{c}" for c in p.collections]
    if p.pick == 1:
        tags.append("Lightroom|picked")
    elif p.pick == -1:
        tags.append("Lightroom|rejected")
    if p.virtual_copies:
        tags.append("Lightroom|had virtual copies")
        notes.append(f"{p.virtual_copies} virtual cop{'y' if p.virtual_copies == 1 else 'ies'} not exported")
    if tags:
        leafs = []
        for t in tags:
            leaf = t.split("|")[-1]
            if leaf not in leafs:
                leafs.append(leaf)
        children.append(li_block("dc:subject", "Bag", leafs))
        children.append(li_block("lr:hierarchicalSubject", "Bag", tags))

    # darktable checks stEvt:softwareAgent contains "Lightroom".
    children.append(
        "   <xmpMM:History>\n    <rdf:Seq>\n"
        "     <rdf:li stEvt:action=\"saved\" stEvt:softwareAgent=\"Adobe Photoshop Lightroom "
        f"(exported by lrrecover {VERSION})\"/>\n"
        "    </rdf:Seq>\n   </xmpMM:History>")

    ns_decl = "\n".join(f"    xmlns:{k}=\"{v}\"" for k, v in NS.items() if k not in ("x", "rdf"))
    attr_text = "\n".join(f"    {k}={quoteattr(v)}" for k, v in attrs)
    xml = (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="lrrecover">\n'
        ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '  <rdf:Description rdf:about=""\n'
        f"{ns_decl}"
        + (f"\n{attr_text}" if attr_text else "")
        + ">\n"
        + "\n".join(children)
        + "\n  </rdf:Description>\n </rdf:RDF>\n</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    )
    return xml, notes


# ---------------------------------------------------------------------------
# Finding photos that moved (new drive letter, new folder)
# ---------------------------------------------------------------------------

def _path_parts(path: str) -> list[str]:
    """'D:/Photos/2014/' -> ['Photos', '2014'];  '/Volumes/Disk/Pics' -> ['Volumes', 'Disk', 'Pics']"""
    p = path.replace("\\", "/")
    p = re.sub(r"^[A-Za-z]:", "", p)
    if p.startswith("//"):  # \\server\share\...
        p = "/".join(p.lstrip("/").split("/")[2:])
    return [x for x in p.split("/") if x]


def available_drives() -> list[str]:
    if os.name == "nt":
        import ctypes
        try:
            mask = ctypes.windll.kernel32.GetLogicalDrives()
        except Exception:
            mask = (1 << 26) - 1
        # Skip A: and B: (floppy prompts on some machines).
        return [f"{chr(65 + i)}:/" for i in range(2, 26)
                if mask >> i & 1 and os.path.exists(f"{chr(65 + i)}:/")]
    out = ["/"]
    for parent in ("/Volumes", "/media", "/mnt", "/run/media"):
        if os.path.isdir(parent):
            for d in os.listdir(parent):
                full = os.path.join(parent, d)
                if os.path.isdir(full):
                    out.append(full)
                    if parent not in ("/media", "/run/media"):
                        continue
                    # Linux mounts drives at /media/<user>/<disk>
                    try:
                        out += [os.path.join(full, s) for s in os.listdir(full)
                                if os.path.isdir(os.path.join(full, s))]
                    except OSError:
                        pass
    return out


def _candidate_roots(old_root: str, search_dirs: list[str]):
    """New places the old root folder might be now. Tries the same folder path on
    every search folder / drive, then with leading folders dropped
    (D:/Users/Bob/Pictures/ -> F:/Pictures/ -> F:/)."""
    parts = _path_parts(old_root)
    seen = set()
    for base in list(search_dirs) + available_drives():
        base = base.replace("\\", "/").rstrip("/") + "/"
        for k in range(len(parts) + 1):
            cand = base + "/".join(parts[k:])
            cand = cand.rstrip("/") + "/"
            key = cand.lower()
            if key not in seen:
                seen.add(key)
                yield cand


def find_moved_roots(photos: list[Photo], search_dirs: list[str] | None = None,
                     samples: int = 8) -> list[tuple[str, str, int]]:
    """For each catalog root folder whose photos are missing, look for where they
    are now. Returns [(old_root, new_root, photo_count)]."""
    by_root: dict[str, list[Photo]] = {}
    for p in photos:
        by_root.setdefault(p.root, []).append(p)
    found = []
    for root, group in by_root.items():
        missing = [p for p in group if not os.path.exists(p.path)]
        if not missing or len(missing) < len(group) / 2:
            continue  # nothing moved (a few deleted files don't count)
        step = max(1, len(missing) // samples)
        sample = missing[::step][:samples]
        need = max(1, int(len(sample) * 0.6 + 0.999))
        for cand in _candidate_roots(root, search_dirs or []):
            if cand.lower() == root.lower() or not os.path.isdir(cand):
                continue
            hits = sum(os.path.exists(cand + p.rel) for p in sample)
            if hits >= need:
                found.append((root, cand, len(group)))
                break
    return found


def apply_remaps(photos: list[Photo], remaps: list[tuple[str, str]]) -> None:
    for p in photos:
        full = (p.root + p.rel)
        for old, new in remaps:
            old_n = old.replace("\\", "/")
            if full.lower().startswith(old_n.lower()):
                new_n = new.replace("\\", "/")
                full = new_n + full[len(old_n):]
                # keep root in sync so later detection groups correctly
                if p.root.lower().startswith(old_n.lower()):
                    p.root = new_n + p.root[len(old_n):]
                p.path = os.path.normpath(full)
                break


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def sidecar_path(photo_path: str, out_dir: str | None, common_root: str | None) -> str:
    base = os.path.splitext(photo_path)[0] + ".xmp"
    if not out_dir:
        return base
    rel = os.path.relpath(base, common_root) if common_root else os.path.basename(base)
    if rel.startswith(".."):
        rel = re.sub(r"[:\\/]+", "_", base)
    return os.path.join(out_dir, rel)


@dataclass
class Options:
    catalog: str
    dry_run: bool = False
    remaps: list = field(default_factory=list)        # [(old_prefix, new_prefix)]
    auto_find: bool = True
    search_dirs: list = field(default_factory=list)   # extra places to look
    out_dir: str | None = None
    overwrite: bool = False
    edited_only: bool = False
    collections: bool = True
    report: str = "lrrecover-report.csv"


@dataclass
class Result:
    photos: int = 0
    edited: int = 0
    written: int = 0
    would_write: int = 0
    missing_photo: int = 0
    exists: int = 0
    name_clash: int = 0
    not_edited: int = 0
    moves: list = field(default_factory=list)
    report: str = ""
    cancelled: bool = False


def run(opts: Options, log=print, confirm_moves=None) -> Result:
    """Do the whole job. `log` gets progress lines. `confirm_moves` is called with
    the detected [(old, new, count)] and returns False to cancel."""
    res = Result()
    log(f"Reading catalog (working on a copy): {opts.catalog}")
    with tempfile.TemporaryDirectory(prefix="lrrecover-") as tmp:
        con = open_catalog_copy(opts.catalog, tmp)
        try:
            photos, warnings = read_catalog(con)
        finally:
            con.close()
    for w in warnings:
        log("warning: " + w)
    res.photos = len(photos)
    res.edited = sum(1 for p in photos if p.develop)
    log(f"Found {res.photos} photos ({res.edited} with edits).")

    if opts.remaps:
        apply_remaps(photos, opts.remaps)

    if opts.auto_find:
        log("Checking photo locations...")
        moves = find_moved_roots(photos, opts.search_dirs)
        for old, new, n in moves:
            log(f"  Moved: {old}  ->  {new}  ({n} photos)")
        if moves:
            if confirm_moves and not confirm_moves(moves):
                log("Cancelled.")
                res.cancelled = True
                return res
            apply_remaps(photos, [(o, n) for o, n, _ in moves])
            res.moves = moves

    by_sidecar: dict[str, list[Photo]] = {}
    paths = [p.path for p in photos]
    try:
        common_root = os.path.commonpath(paths) if paths else None
    except ValueError:  # different drives
        common_root = None
    for p in photos:
        by_sidecar.setdefault(sidecar_path(p.path, opts.out_dir, common_root).lower(), []).append(p)

    rows = []
    for key in sorted(by_sidecar):
        # IMG_1.CR2 + IMG_1.JPG would share IMG_1.xmp: keep the RAW.
        group = sorted(by_sidecar[key], key=lambda p: RAW_PRIORITY.get(p.file_format, 9))
        for loser in group[1:]:
            res.name_clash += 1
            rows.append([loser.path, "", "skipped_name_clash",
                         f"shares sidecar name with {os.path.basename(group[0].path)}"])
        p = group[0]
        out = sidecar_path(p.path, opts.out_dir, common_root)
        if opts.edited_only and not p.develop:
            res.not_edited += 1
            rows.append([p.path, out, "skipped_not_edited", ""])
            continue
        if not os.path.exists(p.path):
            res.missing_photo += 1
            if not opts.out_dir:  # with --out-dir, still write; can be copied later
                rows.append([p.path, out, "photo_not_found", ""])
                continue
        xml, notes = build_xmp(p, include_collections=opts.collections)
        if os.path.exists(out) and not opts.overwrite:
            res.exists += 1
            rows.append([p.path, out, "skipped_xmp_exists", ""])
            continue
        if opts.dry_run:
            res.would_write += 1
            rows.append([p.path, out, "would_write", "; ".join(notes)])
            continue
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        if os.path.exists(out):
            shutil.copy2(out, out + ".bak")
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(xml)
        res.written += 1
        rows.append([p.path, out, "written", "; ".join(notes)])

    with open(opts.report, "w", encoding="utf-8", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["photo", "sidecar", "status", "notes"])
        wr.writerows(rows)
    res.report = os.path.abspath(opts.report)

    log("")
    if opts.dry_run:
        log(f"Sidecars that would be written: {res.would_write}")
    else:
        log(f"Sidecars written:         {res.written}")
    log(f"Photo file not found:     {res.missing_photo}")
    log(f"Skipped, .xmp exists:     {res.exists}")
    log(f"Skipped, same name (JPG next to RAW): {res.name_clash}")
    if opts.edited_only:
        log(f"Skipped, not edited:      {res.not_edited}")
    log(f"Report: {res.report}")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Write Lightroom catalog edits as XMP sidecars that darktable can import.")
    ap.add_argument("catalog", help="path to the .lrcat file")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't write anything, just report what would happen")
    ap.add_argument("--remap", action="append", default=[], metavar="OLD=NEW",
                    help="photos moved: replace path prefix OLD with NEW (repeatable)")
    ap.add_argument("--photos-dir", action="append", default=[], metavar="DIR",
                    help="where the photos are now, if not found automatically (repeatable)")
    ap.add_argument("--no-auto-find", action="store_true",
                    help="don't search other drives for photos that moved")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="accept detected photo locations without asking")
    ap.add_argument("--out-dir", help="write sidecars into this folder instead of next to the photos")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace existing .xmp files (the old one is kept as .xmp.bak)")
    ap.add_argument("--edited-only", action="store_true",
                    help="only photos with develop settings")
    ap.add_argument("--no-collections", action="store_true",
                    help="don't turn collections into 'Lightroom|collections|...' tags")
    ap.add_argument("--report", default="lrrecover-report.csv",
                    help="CSV report path (default: %(default)s)")
    ap.add_argument("--version", action="version", version=VERSION)
    args = ap.parse_args(argv)

    if not os.path.isfile(args.catalog):
        print(f"Catalog not found: {args.catalog}", file=sys.stderr)
        return 2
    remaps = []
    for r in args.remap:
        if "=" not in r:
            print(f"--remap needs OLD=NEW, got {r!r}", file=sys.stderr)
            return 2
        remaps.append(tuple(r.split("=", 1)))

    def confirm(moves):
        if args.yes or args.dry_run or not sys.stdin.isatty():
            return True
        try:
            return input("Use these new locations? [Y/n] ").strip().lower() in ("", "y", "yes")
        except EOFError:
            return True

    opts = Options(catalog=args.catalog, dry_run=args.dry_run, remaps=remaps,
                   auto_find=not args.no_auto_find, search_dirs=args.photos_dir,
                   out_dir=args.out_dir, overwrite=args.overwrite,
                   edited_only=args.edited_only, collections=not args.no_collections,
                   report=args.report)
    res = run(opts, confirm_moves=confirm)
    if res.missing_photo:
        print("\nSome photos weren't found. Plug in the drive they're on, or use "
              "--photos-dir \"F:/Photos\". The report lists the expected paths.")
    return 1 if res.cancelled else 0


if __name__ == "__main__":
    sys.exit(main())
