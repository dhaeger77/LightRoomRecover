"""Simple window for lrrecover: pick the catalog, Preview, then Write."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import lrrecover

TITLE = "Lightroom to darktable recovery"


def report_path() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    folder = docs if os.path.isdir(docs) else os.path.expanduser("~")
    return os.path.join(folder, f"lrrecover-report-{time.strftime('%Y%m%d-%H%M%S')}.csv")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(TITLE)
        root.minsize(720, 480)
        self.q: queue.Queue = queue.Queue()
        self.last_report = None
        self.busy = False

        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text="1. Lightroom catalog(s):").grid(row=0, column=0, sticky="nw", **pad)
        self.catalog = tk.StringVar()
        catf = ttk.Frame(frm)
        catf.grid(row=0, column=1, sticky="ew", **pad)
        catf.columnconfigure(0, weight=1)
        ttk.Entry(catf, textvariable=self.catalog).grid(row=0, column=0, sticky="ew")
        ttk.Label(catf, foreground="gray",
                  text="One or more .lrcat files (separated by ;), or a folder to search. "
                       "Several catalogs are combined.").grid(row=1, column=0, sticky="w")
        catb = ttk.Frame(frm)
        catb.grid(row=0, column=2, sticky="n", **pad)
        ttk.Button(catb, text="Files...", command=self.pick_catalog).pack(fill="x")
        ttk.Button(catb, text="Search folder...", command=self.pick_catalog_folder).pack(fill="x", pady=(4, 0))

        ttk.Label(frm, text="2. Photos folder (optional):").grid(row=1, column=0, sticky="w", **pad)
        self.photos = tk.StringVar()
        ttk.Entry(frm, textvariable=self.photos).grid(row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse...", command=self.pick_photos).grid(row=1, column=2, **pad)
        ttk.Label(frm, foreground="gray",
                  text="Leave empty to search all drives. Pick the external drive or "
                       "photo folder if they aren't found.").grid(
            row=2, column=1, columnspan=2, sticky="w", padx=10)

        opts = ttk.Frame(frm)
        opts.grid(row=3, column=0, columnspan=3, sticky="w", **pad)
        self.overwrite = tk.BooleanVar(value=False)
        self.edited_only = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Replace existing .xmp files (keeps a .bak copy)",
                        variable=self.overwrite).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(opts, text="Only photos with edits",
                        variable=self.edited_only).pack(side="left")

        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        self.preview_btn = ttk.Button(btns, text="3. Preview (writes nothing)",
                                      command=lambda: self.start(dry_run=True))
        self.preview_btn.pack(side="left", padx=(0, 8))
        self.write_btn = ttk.Button(btns, text="4. Write sidecar files",
                                    command=lambda: self.start(dry_run=False))
        self.write_btn.pack(side="left", padx=(0, 8))
        self.report_btn = ttk.Button(btns, text="Open report", command=self.open_report,
                                     state="disabled")
        self.report_btn.pack(side="left", padx=(0, 8))
        self.smart_btn = ttk.Button(btns, text="Smart collections list",
                                    command=lambda: self.open_file(self.last_smart),
                                    state="disabled")
        self.smart_btn.pack(side="left")
        self.last_smart = None

        logf = ttk.Frame(frm)
        logf.grid(row=5, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(5, weight=1)
        mono = (("Consolas", 10) if os.name == "nt"
                else ("Menlo", 12) if sys.platform == "darwin" else ("Courier", 11))
        self.log = tk.Text(logf, height=16, wrap="word", state="disabled", font=mono)
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.write_line("Your Lightroom catalog is only read, never changed.\n"
                        "Close Lightroom first if it is open.\n")
        root.after(100, self.poll)

    # -- UI helpers --------------------------------------------------------
    def pick_catalog(self):
        # macOS file dialogs grey out files that don't match, and the extension
        # filter is unreliable there, so allow everything on Mac.
        types = ([("All files", "*")] if sys.platform == "darwin"
                 else [("Lightroom catalog", "*.lrcat *.lrdb"), ("All files", "*.*")])
        files = filedialog.askopenfilenames(title="Choose the Lightroom catalog(s)", filetypes=types)
        if files:
            self.catalog.set("; ".join(os.path.normpath(f) for f in files))

    def pick_catalog_folder(self):
        d = filedialog.askdirectory(title="Folder to search for Lightroom catalogs")
        if not d:
            return
        found = lrrecover.find_catalogs(d)
        if not found:
            messagebox.showinfo(TITLE, "No Lightroom catalogs (.lrcat) found in that folder.\n\n"
                                       "(Lightroom's Backups folders are skipped on purpose.)")
            return
        self.catalog.set("; ".join(os.path.normpath(f) for f in found))
        self.write_line(f"\nFound {len(found)} catalog{'s' if len(found) != 1 else ''}:")
        for f in found:
            self.write_line("  " + f)

    def pick_photos(self):
        d = filedialog.askdirectory(title="Where are the photos now?")
        if d:
            self.photos.set(os.path.normpath(d))

    def write_line(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_busy(self, busy: bool):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.preview_btn.configure(state=state)
        self.write_btn.configure(state=state)
        self.root.configure(cursor="watch" if busy else "")

    def open_report(self):
        self.open_file(self.last_report)

    @staticmethod
    def open_file(path):
        if not path or not os.path.exists(path):
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.write_line(payload)
                elif kind == "confirm":
                    moves, event, answer = payload
                    lines = "\n".join(
                        f"{o}\n   is now at  {n}   "
                        + (f"({c:,} photos)" if c == t else f"({c:,} of {t:,} photos)")
                        for o, n, c, t in moves)
                    answer.append(messagebox.askyesno(
                        TITLE, "These photos have moved since Lightroom last saw them:\n\n"
                               f"{lines}\n\nUse the new locations?"))
                    event.set()
                elif kind == "done":
                    self.finish(*payload)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    # -- work ---------------------------------------------------------------
    def start(self, dry_run: bool):
        items = [c.strip().strip('"') for c in self.catalog.get().split(";") if c.strip()]
        missing = [c for c in items if not os.path.exists(c)]
        if not items or missing:
            messagebox.showwarning(
                TITLE, "Choose the Lightroom catalog(s) first." if not items
                else "Can't find:\n" + "\n".join(missing))
            return
        cats = lrrecover.expand_catalog_args(items)
        if not cats:
            messagebox.showwarning(TITLE, "No Lightroom catalogs (.lrcat) found.")
            return
        if not dry_run and not messagebox.askyesno(
                TITLE, "Write .xmp sidecar files next to the photos?\n\n"
                       "(Run Preview first if you haven't.)"):
            return
        photos = self.photos.get().strip().strip('"')
        opts = lrrecover.Options(
            catalogs=cats, dry_run=dry_run, search_dirs=[photos] if photos else [],
            overwrite=self.overwrite.get(), edited_only=self.edited_only.get(),
            report=report_path())
        self.set_busy(True)
        self.write_line("\n" + ("=== Preview ===" if dry_run else "=== Writing sidecars ==="))
        threading.Thread(target=self.worker, args=(opts,), daemon=True).start()

    def worker(self, opts):
        def log(msg):
            self.q.put(("log", msg))

        def confirm(moves):
            if opts.dry_run:
                return True
            ev, ans = threading.Event(), []
            self.q.put(("confirm", (moves, ev, ans)))
            ev.wait()
            return bool(ans and ans[0])

        try:
            res = lrrecover.run(opts, log=log, confirm_moves=confirm)
            self.q.put(("done", (opts, res, None)))
        except SystemExit as e:  # read_catalog uses SystemExit for "not a catalog"
            self.q.put(("done", (opts, None, str(e))))
        except Exception as e:  # show anything unexpected instead of dying silently
            self.q.put(("done", (opts, None, f"{type(e).__name__}: {e}")))

    def finish(self, opts, res, error):
        self.set_busy(False)
        if error:
            self.write_line("ERROR: " + error)
            messagebox.showerror(TITLE, error)
            return
        if res.cancelled:
            return
        self.last_report = res.report
        self.report_btn.configure(state="normal")
        self.last_smart = res.smart_listing or None
        self.smart_btn.configure(state="normal" if self.last_smart else "disabled")
        if res.missing_photo:
            self.write_line(
                f"\n{res.missing_photo} photos were not found. Plug in the drive they're on, "
                "or choose their folder in step 2, then try again.")
        if opts.dry_run:
            self.write_line("\nPreview done. Nothing was written. Click 'Write sidecar files' to do it.")
        else:
            self.write_line("\nDone. Now import the photo folders into darktable.")
            messagebox.showinfo(TITLE, f"Wrote {res.written} sidecar files.\n\n"
                                       "Now import the photo folders into darktable.")


def self_test(out_file: str | None) -> int:
    """Used by the build machines: read a tiny catalog, write a sidecar, open and
    close a Tk window. Exit code 0 = OK. Result also written to out_file."""
    import platform
    import shutil
    import sqlite3
    import tempfile
    tmp = tempfile.mkdtemp(prefix="lrrecover-selftest-")
    try:
        photo_dir = os.path.join(tmp, "photos")
        os.makedirs(photo_dir)
        open(os.path.join(photo_dir, "IMG_1.CR2"), "wb").close()
        cat = os.path.join(tmp, "t.lrcat")
        con = sqlite3.connect(cat)
        con.executescript(
            "CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY, absolutePath);"
            "CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY, rootFolder, pathFromRoot);"
            "CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, folder, baseName, extension);"
            "CREATE TABLE Adobe_images (id_local INTEGER PRIMARY KEY, rootFile, masterImage, rating);"
            "CREATE TABLE Adobe_imageDevelopSettings (image, text);")
        con.execute("INSERT INTO AgLibraryRootFolder VALUES (1, ?)",
                    (photo_dir.replace("\\", "/") + "/",))
        con.execute("INSERT INTO AgLibraryFolder VALUES (1, 1, '')")
        con.execute("INSERT INTO AgLibraryFile VALUES (1, 1, 'IMG_1', 'CR2')")
        con.execute("INSERT INTO Adobe_images VALUES (1, 1, NULL, 3)")
        con.execute("INSERT INTO Adobe_imageDevelopSettings VALUES (1, 's = { Exposure2012 = 0.5, }')")
        con.commit()
        con.close()
        res = lrrecover.run(lrrecover.Options(catalog=cat, auto_find=False,
                                              report=os.path.join(tmp, "r.csv")),
                            log=lambda m: None)
        with open(os.path.join(photo_dir, "IMG_1.xmp"), encoding="utf-8") as fh:
            xmp = fh.read()
        assert res.written == 1 and 'crs:Exposure2012="0.5"' in xmp, "sidecar check failed"
        root = tk.Tk()
        root.withdraw()
        App(root)
        root.update()
        root.destroy()
        msg = f"self-test OK on {platform.machine()} (Tk {tk.TkVersion}, lrrecover {lrrecover.VERSION})"
        code = 0
    except Exception as e:  # report any failure through the exit code
        msg = f"self-test FAILED on {platform.machine()}: {type(e).__name__}: {e}"
        code = 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if out_file:
        with open(out_file, "w", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    try:
        print(msg)
    except Exception:  # windowed builds may have no console
        pass
    return code


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test(sys.argv[2] if len(sys.argv) > 2 else None))
    root = tk.Tk()
    if os.name == "nt":  # sharp text on high-DPI Windows; macOS handles Retina itself
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:
            root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72.0)
        except tk.TclError:
            pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
