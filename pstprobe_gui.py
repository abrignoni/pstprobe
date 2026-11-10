#!/usr/bin/env python3
"""pstprobe window: a read-only tkinter window over pstprobe.py.

Standard library only, like the reader it drives. What it shows comes from the same library
calls the command line makes, and its exports are written by the same code as `pstprobe export`
and `pstprobe extract`, so anything seen or saved here can be reproduced there.

    python3 pstprobe_gui.py                     open the window
    python3 pstprobe_gui.py FILE                open it with this PST or OST loaded
    python3 pstprobe_gui.py --selfcheck [FILE]  build the window, load the file, exit (no window shown)

The file is opened read-only and never written. There are no edit controls. The only file the
window writes on its own is its size, position and colour mode, in the user's settings folder.

Author: @AlexisBrignoni, Claude. MIT licence.
"""

import argparse
import json
import os
import queue
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pstprobe  # noqa: E402  pylint: disable=wrong-import-position
import pstprobe_theme  # noqa: E402  pylint: disable=wrong-import-position

_PLACEHOLDER = '\x00loading'
_MAC = sys.platform == 'darwin'
_MOD = 'Command' if _MAC else 'Control'
_MOD_LABEL = 'Cmd' if _MAC else 'Ctrl'
_PAGE_ROWS = 500
_STRIPES = {'ok': '#2e7d32', 'info': '#1565c0', 'warn': '#ef6c00', 'bad': '#c62828'}

# Columns of the item list: two times first, then what identifies the item.
_TIME_TAGS = ((0x0E06, 'Delivered (UTC)'), (0x3008, 'Modified (UTC)'))
# Columns of the recipient table, by property ID ([MS-OXPROPS] names in the headings' tips).
_RECIPIENT_COLUMNS = ((0x0C15, 'Type (0x0C15)', 90), (0x3001, 'Display name', 220),
                      (0x3002, 'Address type', 100), (0x3003, 'Address', 260), (0x39FE, 'SMTP address', 220))


def _settings_path():
    if _MAC:
        base = os.path.expanduser('~/Library/Application Support')
    elif os.name == 'nt':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    return os.path.join(base, 'pstprobe', 'window.json')


def _load_settings():
    try:
        with open(_settings_path(), encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_settings(settings):
    path = _settings_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(settings, handle)
    except OSError:
        pass  # the window's size is a convenience, never worth an error


def _dpi_aware():
    """On Windows, ask to be drawn at the screen's own resolution instead of scaled up (blurry)."""
    if os.name != 'nt':
        return
    try:
        import ctypes  # pylint: disable=import-outside-toplevel
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError, ImportError):
        pass


def _text(value):
    """A property value as one line of text for a table cell."""
    value = pstprobe.jsonable(value)
    if isinstance(value, list):
        return '; '.join(str(v) for v in value)
    if value is None:
        return ''
    text = str(value)
    return text if len(text) <= 400 else text[:400] + '... (%d characters)' % len(text)


def _time(message, pid):
    try:
        value = message.get(pid)
    except pstprobe.DAMAGE_ERRORS:
        return ''
    return value.strftime('%Y-%m-%d %H:%M:%S') if hasattr(value, 'strftime') else ''


def item_summary(message):
    """The row the item list shows for one item."""
    try:
        recipients = len(message.recipients())
    except pstprobe.DAMAGE_ERRORS:
        recipients = '?'
    try:
        attachments = len(message.attachment_nids())
    except pstprobe.DAMAGE_ERRORS:
        attachments = '?'
    return ([_time(message, pid) for pid, _ in _TIME_TAGS]
            + [message.message_class or '', _text(message.subject), recipients, attachments,
               '0x%x' % message.nid])


def item_detail(message):
    """What the lower tabs show for one item, read off the main thread."""
    pst = message.pst
    props = [('0x%04x' % pid, pstprobe.property_name(pid, pst) or '', '0x%04x' % ptype, _text(value))
             for pid, ptype, value in message.pc.items()]
    try:
        recipients = [[_text(r.get(pid)) for pid, _l, _w in _RECIPIENT_COLUMNS] for r in message.recipients()]
    except pstprobe.DAMAGE_ERRORS as error:
        recipients = [['error: %s' % error, '', '', '', '']]
    attachments = []
    try:
        for a in message.attachments():
            if a.is_embedded_message:
                em = a.embedded_message()
                kind, size = 'attached item', ''
                name = (em.message_class or '') + ': ' + _text(em.subject) if em else ''
            else:
                kind, size, name = 'file', a.data_size(), _text(a.filename)
            attachments.append(['0x%x' % a.nid, name, kind, _text(a.method), _text(a.size), _text(size)])
    except pstprobe.DAMAGE_ERRORS as error:
        attachments.append(['', 'error: %s' % error, '', '', '', ''])
    return {'props': props, 'recipients': recipients, 'attachments': attachments,
            'bodies': item_bodies(message)}


# Windows code page numbers that Python does not know as "cp<number>".
_CODEPAGES = {20127: 'ascii', 28591: 'latin-1', 28592: 'iso8859-2', 28595: 'iso8859-5',
              28597: 'iso8859-7', 28599: 'iso8859-9', 28605: 'iso8859-15', 20866: 'koi8-r',
              21866: 'koi8-u', 50220: 'iso2022-jp', 51932: 'euc-jp', 51949: 'euc-kr',
              54936: 'gb18030', 52936: 'hz', 65000: 'utf-7', 65001: 'utf-8'}


def _decode_html(data, codepage=None):
    """PidTagBodyHtml as text: by PidTagInternetCodepage ([MS-OXPROPS] 2.747) when the item
    stores one Python can decode with, else UTF-8, else cp1252 with replacement."""
    if isinstance(data, str):
        return data
    if isinstance(codepage, int):
        try:
            return data.decode(_CODEPAGES.get(codepage, 'cp%d' % codepage))
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('cp1252', errors='replace')


def _shown(text):
    # Tk's Text widget drops NUL characters (an RTF body can end with one, counted in its
    # RAWSIZE), so they are left out on purpose here; Save Body writes the stored bytes.
    return text.replace('\0', '')


# kind -> (button label, how to read it, how to show it, file extension)
_BODY_KINDS = (
    ('text', 'Plain text (0x1000)', lambda m: m.body(), _shown, '.txt'),
    ('html', 'HTML (0x1013)', lambda m: m.html_body(), None, '.html'),
    ('rtf', 'RTF (0x1009)', lambda m: m.rtf_body(),
     lambda v: _shown(v.decode('latin-1')), '.rtf'),
    ('from-rtf', 'Text or HTML in the RTF', None, None, None),
)


def item_bodies(message):
    """kind -> (text to show, bytes to save, file extension) for each body the item stores,
    and for the text or HTML its RTF body encapsulates; a damaged one shows its error and
    saves nothing."""
    out = {}
    for kind, _label, read, show, _ext in _BODY_KINDS:
        if kind == 'from-rtf':
            try:
                inner, text = message.rtf_original()
            except pstprobe.DAMAGE_ERRORS as error:
                if 'rtf' in out and out['rtf'][1] is not None:
                    out[kind] = ('error: %s' % error, None, None)
                continue
            if inner is not None:
                out[kind] = (_shown(text), pstprobe.rtf_original_bytes(inner, text),
                             '.html' if inner == 'html' else '.txt')
            continue
        try:
            value = read(message)
        except pstprobe.DAMAGE_ERRORS as error:
            out[kind] = ('error: %s' % error, None, None)
            continue
        if value is None:
            continue
        raw = value.encode('utf-8') if isinstance(value, str) else bytes(value)
        if kind == 'html':
            try:
                codepage = message.get(0x3FDE)  # PidTagInternetCodepage
            except pstprobe.DAMAGE_ERRORS:
                codepage = None
            out[kind] = (_shown(_decode_html(value, codepage)), raw, _ext)
        else:
            out[kind] = (show(value), raw, _ext)
    return out


class Window:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Folder tree, item list, and the selected item's properties, recipients, attachments, body."""

    def __init__(self, root, settings=None):
        self.root = root
        self.pst = None
        self.folders = {}        # tree item id -> Folder
        self.items_nids = []     # NIDs of the selected folder's items
        self.page = 0
        self.message = None      # the selected item
        self.jobs = queue.Queue()
        self.cancel_flag = threading.Event()
        self.busy = False
        self.sorts = {}
        self.settings = settings if settings is not None else {}
        root.title('pstprobe')
        width = min(1280, root.winfo_screenwidth() - 40)
        height = min(800, root.winfo_screenheight() - 200)
        root.geometry(self.settings.get('geometry', f'{width}x{height}'))
        root.minsize(800, 500)
        self.fixed = 'TkFixedFont'
        self.theme = pstprobe_theme.Theme(root)
        self._base_styles()
        self._build_widgets()
        self._menus()
        self._shortcuts()
        root.protocol('WM_DELETE_WINDOW', self.close)
        if self.dark_var.get():
            self.set_dark(True)
        root.after(100, self._drain)

    # -- layout -----------------------------------------------------------------------------

    def _base_styles(self):
        style = ttk.Style(self.root)
        bold = tkfont.nametofont('TkDefaultFont').copy()
        bold.configure(weight='bold', size=bold.cget('size') + 2 if bold.cget('size') > 0 else bold.cget('size') - 2)
        self._bold = bold
        style.configure('Banner.TLabel', padding=(8, 2, 8, 4))
        style.configure('BannerBold.TLabel', padding=(8, 4, 8, 0), font=bold)
        line = max(tkfont.nametofont(f).metrics('linespace') for f in ('TkDefaultFont', 'TkTextFont'))
        style.configure('Treeview', rowheight=line + 6)

    def _table(self, parent, columns):
        frame = ttk.Frame(parent)
        view = ttk.Treeview(frame, columns=[c for c, _l, _w in columns], show='headings', selectmode='browse')
        for column, label, width in columns:
            view.heading(column, text=label, command=lambda c=column, v=view: self.sort(v, c))
            view.column(column, width=width, stretch=width >= 200)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=view.yview)
        across = ttk.Scrollbar(frame, orient='horizontal', command=view.xview)
        view.configure(yscrollcommand=scroll.set, xscrollcommand=across.set)
        across.pack(side='bottom', fill='x')
        scroll.pack(side='right', fill='y')
        view.pack(side='left', fill='both', expand=True)
        return frame, view

    def _build_widgets(self):
        banner = ttk.Frame(self.root, padding=(8, 6, 8, 2))
        banner.pack(fill='x')
        self.stripe = tk.Frame(banner, width=6, background=_STRIPES['info'])
        self.stripe.pack(side='left', fill='y')
        text = ttk.Frame(banner)
        text.pack(side='left', fill='x', expand=True)
        self.banner_title = ttk.Label(text, text='No file open', style='BannerBold.TLabel')
        self.banner_title.pack(fill='x')
        self.banner = ttk.Label(text, text=f'Open a PST or OST file with {_MOD_LABEL}+O.',
                                style='Banner.TLabel', justify='left', wraplength=1200)
        self.banner.pack(fill='x')

        status = ttk.Frame(self.root, padding=(8, 2, 8, 4))
        status.pack(side='bottom', fill='x')
        self.status = ttk.Label(status, text='Ready')
        self.status.pack(side='left', fill='x', expand=True)
        self.progress = ttk.Progressbar(status, mode='indeterminate', length=160)
        self.cancel_button = ttk.Button(status, text='Cancel', command=self.cancel)

        panes = ttk.PanedWindow(self.root, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=8, pady=4)
        self.panes = panes
        left = ttk.Frame(panes)
        self.tree = ttk.Treeview(left, show='tree', selectmode='browse')
        self.tree.column('#0', width=320, minwidth=160, stretch=True)
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<<TreeviewOpen>>', self._on_open)
        self.tree.bind('<<TreeviewSelect>>', self._on_folder)
        panes.add(left, weight=1)

        right = ttk.PanedWindow(panes, orient='vertical')
        panes.add(right, weight=4)
        top = ttk.Frame(right)
        pager = ttk.Frame(top)
        pager.pack(side='bottom', fill='x')
        self.page_label = ttk.Label(pager, text='')
        self.page_label.pack(side='left', padx=4)
        ttk.Button(pager, text='Next', command=lambda: self.show_page(self.page + 1)).pack(side='right')
        ttk.Button(pager, text='Previous', command=lambda: self.show_page(self.page - 1)).pack(side='right')
        columns = [('t%d' % i, label, 140) for i, (_pid, label) in enumerate(_TIME_TAGS)]
        columns += [('class', 'Class', 120), ('subject', 'Subject', 220), ('recipients', 'Recipients', 75),
                    ('attachments', 'Attachments', 85), ('nid', 'NID', 80)]
        frame, self.items = self._table(top, columns)
        frame.pack(fill='both', expand=True)
        self.items.bind('<<TreeviewSelect>>', self._on_item)
        right.add(top, weight=2)

        self.tabs = ttk.Notebook(right)
        right.add(self.tabs, weight=3)
        frame, self.props = self._table(self.tabs, [('id', 'ID', 70), ('name', 'Name', 260),
                                                    ('type', 'Type', 70), ('value', 'Value', 600)])
        self.tabs.add(frame, text='Properties')
        frame, self.recips = self._table(self.tabs, [('c%d' % i, label, w)
                                                     for i, (_p, label, w) in enumerate(_RECIPIENT_COLUMNS)])
        self.tabs.add(frame, text='Recipients')
        att = ttk.Frame(self.tabs)
        buttons = ttk.Frame(att)
        buttons.pack(side='bottom', fill='x', pady=2)
        ttk.Button(buttons, text='Save Selected Attachment...', command=self.save_attachment).pack(side='left')
        ttk.Button(buttons, text='Save All Attachments of This Item...',
                   command=self.save_item_attachments).pack(side='left', padx=6)
        frame, self.atts = self._table(att, [('nid', 'NID', 80), ('name', 'Name', 320), ('kind', 'Kind', 100),
                                             ('method', 'Method (0x3705)', 110), ('attach_size', 'Size (0x0E20)', 110),
                                             ('data', 'Data bytes', 110)])
        frame.pack(fill='both', expand=True)
        self.tabs.add(att, text='Attachments')
        body = ttk.Frame(self.tabs)
        bar = ttk.Frame(body)
        bar.pack(side='top', fill='x', pady=2)
        self.body_kind = tk.StringVar(value='text')
        self.body_buttons = {}
        for kind, label, _read, _show, _ext in _BODY_KINDS:
            button = ttk.Radiobutton(bar, text=label, value=kind, variable=self.body_kind,
                                     command=self._show_body, state='disabled')
            button.pack(side='left', padx=(0, 10))
            self.body_buttons[kind] = button
        self.save_body_button = ttk.Button(bar, text='Save Body...', command=self.save_body)
        self.save_body_button.pack(side='right')
        self.body = tk.Text(body, wrap='word', state='disabled', borderwidth=0, highlightthickness=0)
        self.body.pack(fill='both', expand=True)
        self.bodies = {}
        self.tabs.add(body, text='Body')

    def _menus(self):
        menubar = tk.Menu(self.root, tearoff=False)
        if _MAC:
            app = tk.Menu(menubar, name='apple')
            app.add_command(label='About pstprobe', command=self.about)
            menubar.add_cascade(menu=app)
        files = tk.Menu(menubar, tearoff=False)
        files.add_command(label='Open PST or OST...', accelerator=f'{_MOD_LABEL}+O', command=self.open_dialog)
        files.add_separator()
        files.add_command(label='Save Attachments of Selected Item...', accelerator=f'{_MOD_LABEL}+E',
                          command=self.save_item_attachments)
        files.add_command(label='Save Shown Body of Selected Item...', command=self.save_body)
        files.add_command(label='Export Selected Folder...', command=lambda: self.export_dialog(False))
        files.add_command(label='Export Selected Folder and Subfolders...', command=lambda: self.export_dialog(True))
        if not _MAC:
            files.add_separator()
            files.add_command(label='Exit', command=self.close)
        menubar.add_cascade(label='File', menu=files)
        edit = tk.Menu(menubar, tearoff=False)
        edit.add_command(label='Copy Selected Row', accelerator=f'{_MOD_LABEL}+C', command=self.copy_row)
        menubar.add_cascade(label='Edit', menu=edit)
        view = tk.Menu(menubar, tearoff=False)
        for n, label in enumerate(('Properties', 'Recipients', 'Attachments', 'Body')):
            view.add_command(label=label, accelerator=f'{_MOD_LABEL}+{n + 1}',
                             command=lambda i=n: self.tabs.select(i))
        view.add_separator()
        self.dark_var = tk.BooleanVar(value=bool(self.settings.get('dark', False)))
        view.add_checkbutton(label='Dark Mode', variable=self.dark_var, command=self.toggle_dark)
        menubar.add_cascade(label='View', menu=view)
        helps = tk.Menu(menubar, tearoff=False, name='help')
        if not _MAC:
            helps.add_command(label='About pstprobe', command=self.about)
        helps.add_command(label='Keyboard Shortcuts', command=self.shortcuts_help)
        menubar.add_cascade(label='Help', menu=helps)
        self.root.configure(menu=menubar)
        self.menubar = menubar

    def _shortcuts(self):
        bind = self.root.bind_all
        bind(f'<{_MOD}-o>', lambda _e: self.open_dialog())
        bind(f'<{_MOD}-e>', lambda _e: self.save_item_attachments())
        for n in range(4):
            bind(f'<{_MOD}-Key-{n + 1}>', lambda _e, i=n: self.tabs.select(i))
        bind('<Escape>', lambda _e: self.cancel())
        for widget in (self.items, self.props, self.recips, self.atts):
            widget.bind(f'<{_MOD}-c>', lambda e: (self.copy_row(e.widget), 'break')[1])

    # -- status and background work ---------------------------------------------------------

    def set_status(self, text):
        self.status.configure(text=text)

    def _start_busy(self, text):
        self.busy = True
        self.cancel_flag.clear()
        self.set_status(text)
        self.progress.pack(side='left', padx=6)
        self.progress.start(12)
        self.cancel_button.pack(side='left')
        self.root.configure(cursor='watch')

    def _stop_busy(self, text):
        self.busy = False
        self.progress.stop()
        self.progress.pack_forget()
        self.cancel_button.pack_forget()
        self.root.configure(cursor='')
        self.set_status(text)

    def cancel(self):
        if self.busy:
            self.cancel_flag.set()
            self.set_status('Cancelling...')

    def _run(self, text, work, done):
        """Run work() off the main thread; done(result) runs on it. One task at a time. The
        main thread also reads the file (expanding a folder); the reader's lock makes that safe."""
        if self.busy:
            self.set_status('Busy: wait for the current task, or press Esc to cancel it.')
            return False
        self._start_busy(text)

        def runner():
            try:
                result = work()
            except Exception as error:  # pylint: disable=broad-except
                # anything else, a damaged file's struct.error included, must still end the
                # task, or the window would stay busy for good
                result = error
            self.jobs.put((done, result))
        threading.Thread(target=runner, daemon=True).start()
        return True

    def _drain(self):
        try:
            while True:
                done, result = self.jobs.get_nowait()
                self._finish(done, result)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _finish(self, done, result):
        if isinstance(result, Exception):
            cancelled = str(result) == 'cancelled'
            if isinstance(result, FileExistsError):
                text = f'Will not replace a file that already exists: {result.filename}'
            else:
                text = str(result)
            self._stop_busy('Cancelled.' if cancelled else f'Error: {text}')
            if not cancelled:
                messagebox.showerror('pstprobe', text, parent=self.root)
        else:
            self._stop_busy('Ready')
            done(result)

    def _wait(self):
        """Selfcheck only: finish the running task without Tk's event loop."""
        done, result = self.jobs.get(timeout=300)
        self._finish(done, result)

    # -- opening ----------------------------------------------------------------------------

    def open_dialog(self):
        path = filedialog.askopenfilename(
            title='Open a PST or OST file', parent=self.root,
            filetypes=(('Outlook data files', '*.pst *.ost *.PST *.OST'), ('All files', '*')))
        if path:
            self.open_path(path)

    def open_path(self, path):
        def work():
            pst = pstprobe.open_pst(path)
            root = pst.root_folder()
            first = [(f, self._has_children(f)) for f in root.subfolders()]
            return pst, root, first
        self._run(f'Opening {os.path.basename(path)}...', work, self.show_file)

    @staticmethod
    def _has_children(folder):
        try:
            return bool(folder.subfolder_nids())
        except pstprobe.DAMAGE_ERRORS:
            return False

    def show_file(self, result):
        pst, root, first = result
        if self.pst is not None:
            self.pst.close()
        self.pst = pst
        self.folders.clear()
        self.tree.delete(*self.tree.get_children(''))
        info = pst.info()
        self.root.title(f'pstprobe: {os.path.basename(pst.path or "")}')
        level = 'warn' if info['short_of_header_eof'] else 'ok'
        self.stripe.configure(background=_STRIPES[level])
        self.banner_title.configure(text=f'{os.path.basename(pst.path or "")}: {info["type"]}, '
                                    f'{info["format"]}, encoding {info["crypt"]}')
        text = f'{info["file_size"]:,} bytes, wVer {info["wVer"]}. Read-only.'
        if info['short_of_header_eof']:
            text += (f' The file is shorter than the {info["header_eof"]:,} bytes its header records: '
                     'data past the end is missing, not empty.')
        if info.get('password_set'):
            text += (f' A password is set (PidTagPstPassword {info["password_crc32"]}, a CRC-32 of '
                     'it). It does not protect the data; the file is read without it.')
        elif info.get('password_set') is None and info.get('password_crc32') not in (None, '0x00000000'):
            text += (f' PidTagPstPassword is {info["password_crc32"]}; what that value means in '
                     'an OST is not established.')
        self.banner.configure(text=text)
        top = self.tree.insert('', 'end', text='/ (root)', open=True)
        self.folders[top] = root
        for folder, children in first:
            self._insert(top, folder, children)
        self.tree.selection_set(top)
        self.set_status(f'Opened {pst.path}')

    def _insert(self, parent, folder, children):
        label = folder.name or '(no name)'
        try:
            label += f'  ({len(folder.message_nids())})'
        except pstprobe.DAMAGE_ERRORS:
            label += '  (?)'
        if folder.is_search_folder:
            label += '  [search folder]'
        item = self.tree.insert(parent, 'end', text=label)
        self.folders[item] = folder
        if children:
            self.tree.insert(item, 'end', text='', values=(_PLACEHOLDER,), tags=('placeholder',))
        return item

    def _on_open(self, _event):
        item = self.tree.focus()
        kids = self.tree.get_children(item)
        if len(kids) == 1 and 'placeholder' in self.tree.item(kids[0], 'tags'):
            self.tree.delete(kids[0])
            folder = self.folders[item]
            for sub in folder.subfolders():
                self._insert(item, sub, self._has_children(sub))

    def selected_folder(self):
        sel = self.tree.selection()
        return self.folders.get(sel[0]) if sel else None

    def _on_folder(self, _event):
        folder = self.selected_folder()
        if folder is None:
            return
        try:
            self.items_nids = folder.message_nids()
        except pstprobe.DAMAGE_ERRORS as error:
            self.items_nids = []
            self.set_status(f'Error: {error}')
        self.show_page(0)

    def show_page(self, page):
        pages = max(1, (len(self.items_nids) + _PAGE_ROWS - 1) // _PAGE_ROWS)
        if page < 0 or page >= pages or self.pst is None:
            return
        nids = self.items_nids[page * _PAGE_ROWS:(page + 1) * _PAGE_ROWS]
        pst = self.pst

        def work():
            rows = []
            for nid in nids:
                if self.cancel_flag.is_set():
                    raise pstprobe.PSTError('cancelled')
                try:
                    m = pst.message(nid)
                    rows.append(item_summary(m) if m else ['', '', '', 'node missing', '', '', '0x%x' % nid])
                except pstprobe.DAMAGE_ERRORS as error:
                    rows.append(['', '', '', f'error: {error}', '', '', '0x%x' % nid])
            return rows

        def done(rows):
            self.page = page
            self.items.delete(*self.items.get_children(''))
            for row in rows:
                self.items.insert('', 'end', values=row)
            first = page * _PAGE_ROWS
            self.page_label.configure(text=f'Items {first + 1 if rows else 0}-{first + len(rows)} of '
                                      f'{len(self.items_nids)}' if self.items_nids else 'No items')
            self._clear_detail()
        self._run(f'Reading {len(nids)} items...', work, done)

    def _clear_detail(self):
        for view in (self.props, self.recips, self.atts):
            view.delete(*view.get_children(''))
        self._set_bodies({})
        self.message = None

    def _set_bodies(self, bodies):
        """Enable a button per stored body and show the first of plain text, HTML, RTF."""
        self.bodies = bodies
        for kind, button in self.body_buttons.items():
            button.configure(state='normal' if kind in bodies else 'disabled')
        # the first stored of plain text, HTML, what the RTF wraps, RTF
        first = next((kind for kind in ('text', 'html', 'from-rtf', 'rtf') if kind in bodies), None)
        if first is not None:
            self.body_kind.set(first)
        self._show_body()

    def _show_body(self):
        if not self.bodies:
            self._set_body('' if self.message is None else 'This item stores no body.')
            return
        shown = self.bodies.get(self.body_kind.get())
        self._set_body(shown[0] if shown else '')

    def save_body(self, target=None):
        shown = self.bodies.get(self.body_kind.get())
        if self.message is None or not shown or shown[1] is None:
            self.set_status('Select an item with a body first.')
            return None
        ext = shown[2]
        target = target or filedialog.asksaveasfilename(
            title='Save body', parent=self.root, defaultextension=ext,
            initialfile='%06x_body%s' % (self.message.nid, ext))
        if not target:
            return None
        data = shown[1]

        def work():
            with open(target, 'xb') as out:
                out.write(data)
            return len(data)
        self._run('Saving...', work, lambda n: self.set_status(f'Saved {n:,} bytes to {target}'))
        return target

    def _set_body(self, text):
        self.body.configure(state='normal')
        self.body.delete('1.0', 'end')
        self.body.insert('1.0', text)
        self.body.configure(state='disabled')

    def _on_item(self, _event):
        sel = self.items.selection()
        if not sel or self.pst is None:
            return
        nid = int(self.items.set(sel[0], 'nid'), 16)
        pst = self.pst

        def work():
            m = pst.message(nid)
            if m is None:
                raise pstprobe.CorruptError('item node 0x%x is missing' % nid)
            return m, item_detail(m)

        def done(result):
            m, detail = result
            self._clear_detail()
            self.message = m
            for row in detail['props']:
                self.props.insert('', 'end', values=row)
            for row in detail['recipients']:
                self.recips.insert('', 'end', values=row)
            for row in detail['attachments']:
                self.atts.insert('', 'end', values=row)
            self._set_bodies(detail['bodies'])
        self._run('Reading the item...', work, done)

    # -- export -----------------------------------------------------------------------------

    def save_attachment(self):
        sel = self.atts.selection()
        if self.message is None or not sel:
            self.set_status('Select an attachment first.')
            return
        nid = int(self.atts.set(sel[0], 'nid'), 16)
        att = next((a for a in self.message.attachments() if a.nid == nid), None)
        if att is None or att.is_embedded_message:
            self.set_status('An attached item has no file to save; export its folder to get its properties.')
            return
        target = filedialog.asksaveasfilename(title='Save attachment', parent=self.root,
                                              initialfile=pstprobe.safe_filename(att.filename))
        if target:
            self._run('Saving...', lambda: pstprobe.write_attachment(att, target),
                      lambda n: self.set_status(f'Saved {n:,} bytes to {target}'))

    def save_item_attachments(self, folder=None):
        if self.message is None:
            self.set_status('Select an item first.')
            return
        folder = folder or filedialog.askdirectory(title='Folder to save the attachments in', parent=self.root)
        if not folder:
            return
        m = self.message

        def work():
            written = []
            for name, a in pstprobe.iter_file_attachments(m):
                path = os.path.join(folder, name)
                pstprobe.write_attachment(a, path)
                written.append(path)
            return written
        self._run('Saving attachments...', work,
                  lambda w: self.set_status(f'Saved {len(w)} attachment file(s) to {folder}'))

    def export_dialog(self, recursive, target=None):
        folder = self.selected_folder()
        if folder is None or self.pst is None:
            self.set_status('Select a folder first.')
            return
        target = target or filedialog.askdirectory(title='Folder to export into', parent=self.root)
        if not target:
            return
        out = os.path.join(target, '%s_%06x' % (pstprobe.safe_filename(folder.name, 'folder'), folder.nid))
        pst = self.pst
        self._run('Exporting...', lambda: pstprobe.export_folder(pst, folder, out, recursive,
                                                                  cancel=self.cancel_flag.is_set),
                  lambda c: self.set_status(f'Exported {c["folders"]} folder(s), {c["items"]} items, '
                                            f'{c["attachments"]} attachment files, {c["rtf_bodies"]} RTF bodies to {out}'))
        return out

    # -- copy and sort ----------------------------------------------------------------------

    def copy_row(self, view=None):
        view = view if isinstance(view, ttk.Treeview) else self.root.focus_get()
        if not isinstance(view, ttk.Treeview) or view is self.tree:
            folder = self.selected_folder()
            if folder is not None:
                self._copy(folder.path)
            return
        sel = view.selection()
        if sel:
            self._copy('\t'.join(str(v) for v in view.item(sel[0], 'values')))

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status('Copied: ' + (text if len(text) <= 80 else text[:77] + '...'))

    def sort(self, view, column):
        last, descending = self.sorts.get(view, (None, False))
        descending = not descending if last == column else False
        self.sorts[view] = (column, descending)
        rows = [(view.set(item, column), item) for item in view.get_children('')]

        def key(row):
            text = row[0]
            return (0, int(text), '') if text.isdigit() else (1, 0, text.upper())
        rows.sort(key=key, reverse=descending)
        for position, (_text, item) in enumerate(rows):
            view.move(item, '', position)

    # -- misc -------------------------------------------------------------------------------

    def toggle_dark(self):
        self.set_dark(self.dark_var.get())
        self.settings['dark'] = self.dark_var.get()

    def set_dark(self, dark):
        self.theme.apply(dark)
        self._base_styles()

    def about(self):
        messagebox.showinfo('About pstprobe', (
            f'pstprobe {pstprobe.__version__}\n\nReads Outlook PST and OST files read-only.\n'
            'Python standard library only. MIT licence.\n\nhttps://github.com/abrignoni/pstprobe'),
            parent=self.root)

    def shortcuts_help(self):
        m = _MOD_LABEL
        messagebox.showinfo('Keyboard shortcuts', '\n'.join((
            f'{m}+O  open a PST or OST file', f'{m}+E  save the selected item\'s attachments',
            f'{m}+C  copy the selected row', f'{m}+1 to {m}+4  Properties, Recipients, Attachments, Body',
            'Esc  cancel a running task')), parent=self.root)

    def close(self):
        try:
            self.settings['geometry'] = self.root.geometry()
            _save_settings(self.settings)
        except tk.TclError:
            pass
        if self.pst is not None:
            self.pst.close()
        self.root.destroy()


def selfcheck(path):
    """Build the window, load the file, select the first folder with items and its first item,
    and save that item's attachments and export the folder to a temporary folder.

    It never runs Tk's event loop (python.org 3.14 on macOS ships Tk 9.0.3, which aborts on a
    window that was never shown). Settings are neither read nor written.
    """
    root = tk.Tk()
    root.withdraw()
    window = Window(root, settings={})
    report = {'window': 'built', 'menus': window.menubar.index('end') + 1}
    if path:
        window.open_path(path)
        window._wait()  # pylint: disable=protected-access
        report['folders_shown'] = len(window.folders)
        # breadth-first over the tree, expanding placeholders, to the first folder with items
        queue_items = list(window.tree.get_children(''))
        while queue_items:
            item = queue_items.pop(0)
            window.tree.focus(item)
            window._on_open(None)  # pylint: disable=protected-access
            folder = window.folders[item]
            if folder.message_nids():
                window.tree.selection_set(item)
                window._on_folder(None)  # pylint: disable=protected-access
                window._wait()  # pylint: disable=protected-access
                break
            queue_items.extend(window.tree.get_children(item))
        report['folders_shown'] = len(window.folders)
        rows = window.items.get_children('')
        report['items_shown'] = len(rows)
        # the first item with an attachment, else the first item
        pick = next((r for r in rows if str(window.items.set(r, 'attachments')) not in ('0', '?')),
                    rows[0] if rows else None)
        if pick is not None:
            window.items.selection_set(pick)
            window._on_item(None)  # pylint: disable=protected-access
            window._wait()  # pylint: disable=protected-access
            report['properties_shown'] = len(window.props.get_children(''))
            report['recipients_shown'] = len(window.recips.get_children(''))
            report['attachments_shown'] = len(window.atts.get_children(''))
            report['bodies_shown'] = sorted(window.bodies)
            report['body_chars_shown'] = len(window.body.get('1.0', 'end-1c'))
            with tempfile.TemporaryDirectory() as folder:
                saved = window.save_body(os.path.join(folder, 'body.out'))
                if saved:
                    window._wait()  # pylint: disable=protected-access
                    report['body_bytes_saved'] = os.path.getsize(saved)
                    os.remove(saved)
                window.save_item_attachments(folder)
                window._wait()  # pylint: disable=protected-access
                report['attachment_files_saved'] = len(os.listdir(folder))
                out = window.export_dialog(False, folder)
                window._wait()  # pylint: disable=protected-access
                with open(os.path.join(out, 'items.jsonl'), encoding='utf-8') as handle:
                    report['exported_items'] = sum(1 for _ in handle)
        window.sort(window.items, 'nid')
    for pending in root.tk.splitlist(root.tk.call('after', 'info')):
        root.after_cancel(pending)
    if window.pst is not None:
        window.pst.close()
    root.destroy()
    print(json.dumps(report))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog='pstprobe_gui')
    parser.add_argument('file', nargs='?')
    parser.add_argument('--selfcheck', action='store_true', help='build the window and exit')
    args = parser.parse_args(argv)
    if args.selfcheck:
        return selfcheck(args.file)
    _dpi_aware()
    root = tk.Tk()
    window = Window(root, settings=_load_settings())
    if args.file:
        root.after(200, lambda: window.open_path(args.file))
    root.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
