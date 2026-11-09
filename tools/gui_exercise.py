"""Drive the real pstprobe window through every action and check what it shows.

    python3 tools/gui_exercise.py [--shots DIR] [--items N] FILE [FILE ...]

Unlike `pstprobe_gui.py --selfcheck`, this runs Tk's event loop with the window on screen,
so it needs a display and, on macOS, a Python with Tk 8.6 (Tk 9 aborts there). File and
message dialogs are replaced with stubs that answer from a temporary folder and record what
they were asked; nothing is written outside that folder, and the window's settings file is
not touched.

For each file it opens it, expands every folder, checks each folder's label count, shows
every page of every folder's items, and for up to N items per folder (the first, the last,
and those with attachments first) checks the Properties, Recipients, Attachments and Body
tabs against pstprobe's own answers and switches between the stored bodies. It sorts every
item column both ways, copies a row, switches tabs with the keyboard shortcuts, toggles dark
mode, saves an attachment, all of an item's attachments and a body (and checks the bytes),
checks that a second save to the same name is refused, exports the folder with the most items
and the whole file and compares both with pstprobe.export_folder, tries the busy guard and
cancel, and opens the About and shortcut dialogs. After the last file it opens a damaged copy
of the first and a file that is not a PST, checks that each is refused with a message and
leaves the open file in place, and closes the window.

Prints one JSON report and exits 1 if any check failed. Values are counts and check names
only; no subjects, names or contents.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import tkinter as tk  # noqa: E402
from tkinter import filedialog, messagebox  # noqa: E402

import pstprobe  # noqa: E402
import pstprobe_gui as gui  # noqa: E402


class Dialogs:
    """Stand-ins for the file and message dialogs."""

    def __init__(self):
        self.answers = []   # paths to return from the next file dialogs, in order
        self.shown = []     # (kind, title) of every message box

    def file(self, **_kwargs):
        return self.answers.pop(0) if self.answers else ""

    def message(self, kind):
        def show(title, _text=None, **_kwargs):
            self.shown.append((kind, title))
            return "ok"
        return show

    def install(self):
        filedialog.askopenfilename = self.file
        filedialog.asksaveasfilename = self.file
        filedialog.askdirectory = self.file
        for kind in ("showinfo", "showerror", "showwarning"):
            setattr(messagebox, kind, self.message(kind))


def tree_listing(top):
    out = []
    for base, _dirs, files in os.walk(top):
        for name in files:
            path = os.path.join(base, name)
            out.append((os.path.relpath(path, top), os.path.getsize(path)))
    return sorted(out)


class Exercise:
    def __init__(self, root, window, dialogs, args):
        self.root = root
        self.w = window
        self.d = dialogs
        self.args = args
        self.tmp = tempfile.mkdtemp(prefix="pstprobe-gui-")
        self.report = {"files": [], "failures": [], "checks": 0}
        self.current = None

    # -- helpers ------------------------------------------------------------------------

    def check(self, ok, what):
        self.report["checks"] += 1
        if not ok:
            self.report["failures"].append(("%s: " % self.current if self.current else "") + what)
        return ok

    def idle(self, timeout=600):
        """Let the event loop run until the window's task is done."""
        end = time.monotonic() + timeout
        self.root.update()
        while self.w.busy or not self.w.jobs.empty():
            if time.monotonic() > end:
                self.check(False, "task did not finish in %d s" % timeout)
                return
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def shot(self, name):
        if not self.args.shots or sys.platform != "darwin":
            return
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.update()
        time.sleep(0.4)
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        wd, ht = self.root.winfo_width(), self.root.winfo_height()
        path = os.path.join(self.args.shots, name + ".png")
        subprocess.run(["screencapture", "-x", "-R", "%d,%d,%d,%d" % (x, y - 28, wd, ht + 28), path],
                       check=False, stdin=subprocess.DEVNULL)
        self.root.attributes("-topmost", False)

    def tmp_path(self, *parts):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # -- per file -----------------------------------------------------------------------

    def open_file(self, path):
        self.w.open_path(path)
        self.idle()
        return self.w.pst is not None and os.path.abspath(self.w.pst.path) == os.path.abspath(path)

    def expand_all(self):
        stack = list(self.w.tree.get_children(""))
        while stack:
            item = stack.pop()
            self.w.tree.item(item, open=True)
            self.w.tree.focus(item)
            self.w._on_open(None)  # pylint: disable=protected-access
            stack.extend(k for k in self.w.tree.get_children(item)
                         if "placeholder" not in self.w.tree.item(k, "tags"))
        self.root.update()

    def select_folder(self, item):
        self.w.tree.selection_set(item)
        self.w.tree.see(item)
        self.root.update()
        self.idle()

    def run_file(self, path, index):
        self.current = os.path.basename(path)
        r = {"file": self.current, "folders": 0, "folders_with_items": 0, "pages": 0,
             "items_checked": 0, "bodies_checked": 0, "attachments_saved": 0}
        if not self.check(self.open_file(path), "file did not open"):
            self.report["files"].append(r)
            return
        pst = self.w.pst
        banner = str(self.w.banner.cget("text"))
        info = pst.info()
        self.check((info.get("password_set") is True) == ("A password is set" in banner),
                   "password line in banner")
        self.expand_all()
        expected = {f.nid: f for f in pst.walk_folders()}
        shown = {}
        for item, folder in self.w.folders.items():
            shown.setdefault(folder.nid, []).append(item)
        r["folders"] = len(shown)
        self.check(set(shown) == set(expected), "tree shows %d folders, the file has %d"
                   % (len(shown), len(expected)))
        biggest = None
        for nid, items in sorted(shown.items()):
            folder = expected.get(nid) or self.w.folders[items[0]]
            count = len(folder.message_nids())
            label = self.w.tree.item(items[0], "text")
            if items[0] != self.w.tree.get_children("")[0]:
                self.check("(%d)" % count in label, "folder 0x%x label lacks its count" % nid)
            if not count:
                continue
            r["folders_with_items"] += 1
            if biggest is None or count > biggest[1]:
                biggest = (items[0], count, folder)
            self.select_folder(items[0])
            pages = (count + gui._PAGE_ROWS - 1) // gui._PAGE_ROWS  # pylint: disable=protected-access
            for page in range(pages):
                if page:
                    self.w.show_page(page)
                    self.idle()
                rows = len(self.w.items.get_children(""))
                want = min(gui._PAGE_ROWS, count - page * gui._PAGE_ROWS)  # pylint: disable=protected-access
                self.check(rows == want, "folder 0x%x page %d shows %d rows, expected %d"
                           % (nid, page, rows, want))
                r["pages"] += 1
                if page == 0 or page == pages - 1:
                    self.check_items(r)
        if biggest is not None:
            self.select_folder(biggest[0])
            self.page_one_checks(r)
            self.save_checks(r, biggest[2])
            self.screenshots(index)
            self.export_checks(biggest[2], index)
        self.check_shortcuts_and_dialogs()
        self.report["files"].append(r)

    def screenshots(self, index):
        """With an item and its body showing (save_checks left one selected): the Body tab,
        then the Attachments tab, in light and, for the first file, dark mode."""
        modes = (False, True) if index == 0 else (False,)
        for dark in modes:
            if dark:
                self.w.dark_var.set(True)
                self.w.toggle_dark()
                self.root.update()
            for tab, name in ((3, "body"), (2, "attachments")):
                self.w.tabs.select(tab)
                self.root.update()
                self.shot("%02d-%s-%s" % (index, "dark" if dark else "light", name))
            if dark:
                self.w.dark_var.set(False)
                self.w.toggle_dark()
                self.root.update()

    def check_items(self, r):
        rows = list(self.w.items.get_children(""))
        with_att = [x for x in rows if str(self.w.items.set(x, "attachments")) not in ("0", "?")]
        picks = []
        for x in [rows[0], rows[-1]] + with_att + rows:
            if x not in picks:
                picks.append(x)
            if len(picks) >= self.args.items:
                break
        for row in picks:
            nid = int(self.w.items.set(row, "nid"), 16)
            self.w.items.selection_set(row)
            self.w.items.see(row)
            self.root.update()
            self.idle()
            m = self.w.pst.message(nid)
            if not self.check(self.w.message is not None and self.w.message.nid == nid,
                              "item 0x%x not shown" % nid):
                continue
            r["items_checked"] += 1
            self.check(len(self.w.props.get_children("")) == len(list(m.pc.items())),
                       "item 0x%x property rows" % nid)
            try:
                want_r = len(m.recipients())
            except pstprobe.DAMAGE_ERRORS:
                want_r = 1
            self.check(len(self.w.recips.get_children("")) == want_r, "item 0x%x recipient rows" % nid)
            try:
                want_a = len(m.attachments())
            except pstprobe.DAMAGE_ERRORS:
                want_a = None
            if want_a is not None:
                self.check(len(self.w.atts.get_children("")) == want_a, "item 0x%x attachment rows" % nid)
            bodies = gui.item_bodies(m)
            self.check(set(self.w.bodies) == set(bodies), "item 0x%x body kinds" % nid)
            for kind, button in self.w.body_buttons.items():
                enabled = str(button.cget("state")) != "disabled"
                self.check(enabled == (kind in bodies), "item 0x%x %s button state" % (nid, kind))
                if not enabled:
                    continue
                button.invoke()
                self.root.update()
                text = self.w.body.get("1.0", "end-1c")
                self.check(text == bodies[kind][0], "item 0x%x %s body text" % (nid, kind))
                r["bodies_checked"] += 1
            if not bodies:
                self.check(self.w.body.get("1.0", "end-1c") == "This item stores no body.",
                           "item 0x%x empty body note" % nid)

    def page_one_checks(self, r):
        view = self.w.items
        rows = len(view.get_children(""))
        for column in view["columns"]:
            for _ in range(2):
                self.w.sort(view, column)
                self.root.update()
            self.check(len(view.get_children("")) == rows, "sort by %s changed the rows" % column)
        first = view.get_children("")[0]
        view.xview_moveto(0)
        view.yview_moveto(0)
        self.root.update()
        box = view.bbox(first, "nid")
        self.check(bool(box) and box[0] + box[2] <= view.winfo_width(),
                   "NID column not fully visible at %d px" % view.winfo_width())
        view.selection_set(first)
        self.root.update()
        self.idle()
        self.w.copy_row(view)
        want = "\t".join(str(v) for v in view.item(first, "values"))
        self.check(self.root.clipboard_get() == want, "copied row")
        r["sorted_columns"] = len(view["columns"])

    def save_checks(self, r, folder):
        """Save one attachment, all of an item's attachments, a body, and refuse a second save."""
        for nid in folder.message_nids()[:200]:
            m = self.w.pst.message(nid)
            files = [a for a in m.attachments() if not a.is_embedded_message]
            if files:
                break
        else:
            m, files = None, []
        if m is not None:
            self._select_nid(m.nid)
            att = files[0]
            row = next(x for x in self.w.atts.get_children("")
                       if int(self.w.atts.set(x, "nid"), 16) == att.nid)
            self.w.atts.selection_set(row)
            self.root.update()
            target = self.tmp_path("save%d" % len(self.report["files"]), "one_%06x.bin" % att.nid)
            self.d.answers.append(target)
            self.w.save_attachment()
            self.idle()
            ok = os.path.exists(target) and open(target, "rb").read() == att.data()
            self.check(ok, "saved attachment bytes")
            r["attachments_saved"] += ok
            folder_out = self.tmp_path("save%d" % len(self.report["files"]), "all_%06x" % m.nid, "x")[:-2]
            os.makedirs(folder_out, exist_ok=True)
            self.d.answers.append(folder_out)
            self.w.save_item_attachments()
            self.idle()
            want = sorted(name for name, _a in pstprobe.iter_file_attachments(m))
            self.check(sorted(os.listdir(folder_out)) == want, "saved all attachments of an item")
            r["attachments_saved"] += len(os.listdir(folder_out))
        # a body: the first item of the folder that has one
        for nid in folder.message_nids()[:200]:
            bodies = gui.item_bodies(self.w.pst.message(nid))
            if any(raw is not None for _t, raw in bodies.values()):
                break
        else:
            return
        self._select_nid(nid)
        kind = self.w.body_kind.get()
        target = self.tmp_path("save%d" % len(self.report["files"]), "body_%06x" % nid)
        self.d.answers.append(target)
        self.w.save_body()
        self.idle()
        self.check(os.path.exists(target) and open(target, "rb").read() == bodies[kind][1],
                   "saved body bytes")
        errors = len([k for k, _t in self.d.shown if k == "showerror"])
        self.d.answers.append(target)
        self.w.save_body()
        self.idle()
        self.check(len([k for k, _t in self.d.shown if k == "showerror"]) == errors + 1,
                   "second save to the same name was not refused")
        self.check(str(self.w.status.cget("text")).startswith("Error"), "refusal shown in status")

    def _select_nid(self, nid):
        for row in self.w.items.get_children(""):
            if int(self.w.items.set(row, "nid"), 16) == nid:
                self.w.items.selection_set(row)
                self.w.items.see(row)
                self.root.update()
                self.idle()
                return True
        # not on this page: put it on the page directly
        self.w.items.insert("", "end", values=gui.item_summary(self.w.pst.message(nid)))
        return self._select_nid(nid)

    def export_checks(self, folder, index):
        pst = self.w.pst
        for recursive, label in ((False, "folder"), (True, "whole file")):
            if recursive:
                top = self.w.tree.get_children("")[0]
                self.select_folder(top)
                folder = pst.root_folder()
            target = self.tmp_path("export%d_%s" % (index, label.replace(" ", "")), "x")[:-2]
            os.makedirs(target, exist_ok=True)
            self.d.answers.append(target)
            out = self.w.export_dialog(recursive)
            self.idle()
            direct = self.tmp_path("direct%d_%s" % (index, label.replace(" ", "")), "x")[:-2]
            pstprobe.export_folder(pst, folder, direct, recursive)
            self.check(out is not None and tree_listing(out) == tree_listing(direct),
                       "export of the %s differs from export_folder" % label)
            self.check(str(self.w.status.cget("text")).startswith("Exported"), "export status")

    def check_shortcuts_and_dialogs(self):
        mod = gui._MOD  # pylint: disable=protected-access
        self.w.items.focus_force()
        self.root.update()
        for n in range(4):
            self.w.items.event_generate("<%s-Key-%d>" % (mod, n + 1))
            self.root.update()
            self.check(self.w.tabs.index("current") == n, "shortcut %d selects tab %d" % (n + 1, n))
        before = len(self.d.shown)
        self.w.about()
        self.w.shortcuts_help()
        self.check(len(self.d.shown) == before + 2, "about and shortcuts dialogs")
        # busy guard and cancel: a whole-file export, cancelled at once
        target = self.tmp_path("cancel", "x")[:-2]
        os.makedirs(target, exist_ok=True)
        top = self.w.tree.get_children("")[0]
        self.select_folder(top)
        self.d.answers.append(target)
        self.w.export_dialog(True)
        if self.w.busy:
            self.w.show_page(0)
            self.check(str(self.w.status.cget("text")).startswith("Busy"), "busy guard")
            self.w.items.event_generate("<Escape>")
            self.root.update()
        self.idle()
        status = str(self.w.status.cget("text"))
        self.check(status.startswith("Cancelled") or status.startswith("Exported"),
                   "status after cancel: %s" % status[:40])

    # -- damaged inputs and close -------------------------------------------------------

    def damaged(self, first):
        self.current = "damaged inputs"
        before = self.w.pst
        cut = self.tmp_path("bad", "cut.pst")
        with open(first, "rb") as src, open(cut, "wb") as out:
            out.write(src.read(max(4096, os.path.getsize(first) // 3)))
        notpst = self.tmp_path("bad", "not.pst")
        with open(notpst, "wb") as out:
            out.write(b"this is not an Outlook file\n" * 100)
        for bad in (notpst, cut):
            errors = len([k for k, _t in self.d.shown if k == "showerror"])
            self.w.open_path(bad)
            self.idle()
            opened = self.w.pst is not before
            if opened:
                # a cut file can open (the header and root may be intact); then every folder
                # and item must still be shown or reported, never crash the window
                self.expand_all()
                for item in list(self.w.folders)[:50]:
                    self.select_folder(item)
                    rows = self.w.items.get_children("")
                    if rows:
                        self.w.items.selection_set(rows[0])
                        self.root.update()
                        self.idle()
                self.check(not self.w.busy, "window stuck busy on %s" % os.path.basename(bad))
                self.report["cut_file_opened"] = True
                before = self.w.pst
            else:
                self.check(len([k for k, _t in self.d.shown if k == "showerror"]) == errors + 1,
                           "%s refused without a message" % os.path.basename(bad))
                self.check(self.w.pst is before, "%s replaced the open file" % os.path.basename(bad))
        self.shot("99-after-damaged")

    def close(self):
        saved = {}
        gui._save_settings = lambda s: saved.update(s)  # pylint: disable=protected-access
        self.w.close()
        self.current = "close"
        self.check("geometry" in saved, "settings saved on close")
        shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--shots", help="folder for screenshots (macOS). screencapture takes a screen "
                        "region, so any window in front of this one is captured too")
    parser.add_argument("--items", type=int, default=12, help="items checked per page (default 12)")
    args = parser.parse_args()
    if sys.platform == "darwin" and tk.TkVersion >= 9:
        print("Tk %s on macOS aborts tkinter windows; use a Python with Tk 8.6" % tk.TkVersion,
              file=sys.stderr)
        return 2
    if args.shots:
        os.makedirs(args.shots, exist_ok=True)
    dialogs = Dialogs()
    dialogs.install()
    root = tk.Tk()
    window = gui.Window(root, settings={"geometry": "1240x760+40+60"})
    ex = Exercise(root, window, dialogs, args)
    start = time.monotonic()
    for index, path in enumerate(args.files):
        try:
            ex.run_file(path, index)
        except Exception as error:  # pylint: disable=broad-except
            ex.check(False, "exercise raised %s: %s" % (type(error).__name__, error))
    try:
        ex.damaged(args.files[0])
    except Exception as error:  # pylint: disable=broad-except
        ex.check(False, "damaged-input check raised %s: %s" % (type(error).__name__, error))
    ex.close()
    ex.report["seconds"] = round(time.monotonic() - start, 1)
    ex.report["dialogs"] = sorted({k for k, _t in dialogs.shown})
    print(json.dumps(ex.report, indent=1))
    return 1 if ex.report["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
