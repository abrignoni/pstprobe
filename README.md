# pstprobe

A read-only reader for Outlook PST and OST files. One file, pure Python, standard
library only. No compiler, no network, nothing to install.

It opens a Personal Storage Table (`.pst`) or an Offline Storage Table (`.ost`) and
reads its folders, the items in them (mail, contacts, appointments, tasks, notes,
journal entries and the rest), their recipients and their attachments, including
items attached to other items.

```python
import pstprobe

with pstprobe.open_pst("mailbox.pst") as pst:
    for folder in pst.walk_folders():
        for message in folder.messages():
            print(folder.path, message.message_class, message.subject)
            for att in message.attachments():
                print("   ", att.filename, att.data_size())
```

The file is read with seek and read, one node at a time, through small caches. A
mailbox of several gigabytes is never loaded whole, and an attachment can be copied
out in pieces with `Attachment.iter_data()`.

## Why this exists

The established library for this format is libpff, which is excellent and is LGPL,
ships as a C extension, and needs a build or a wheel for each platform. This reader
exists so an MIT-licensed tool can read a PST with no copyleft dependency and no
install step: copy one file in and it works on Windows, macOS and Linux alike.

Nothing here is derived from libpff or from any other PST implementation. It is
written from Microsoft's published specification and one public format document,
credited below.

## What it reads

| Variant | Header | Tested against |
|---|---|---|
| Unicode PST | wVer 23, 512-byte pages | A PST that Outlook (LTSC 2024) made and filled, and the tests' own files |
| ANSI PST | wVer 14 or 15 | An ANSI PST (wVer 14) that Outlook made, and the tests' own files. No wVer 15 file has been seen |
| Unicode OST, 4 KB pages | wVer 36, blocks compressed with zlib | The OST in NIST CFReDS "Data Leakage Case", and the tests' own files |
| NDB_CRYPT_PERMUTE | bCryptMethod 0x01 | Both Outlook PSTs above |
| NDB_CRYPT_CYCLIC | bCryptMethod 0x02 | The tests' own files only. No real file has been read |
| Not encoded | bCryptMethod 0x00 | The CFReDS OST |

A file encrypted with Windows Information Protection (bCryptMethod 0x10) is refused
with an error that says so, because its content cannot be read without its key. So is
a 4 KB-page file that is both encoded and compressed: no such file has been seen, and
the order of the two steps is not established.

In a 4 KB-page OST, a heap ID splits into a 14-bit index and a 13-bit block index (from
the PFF notes; [MS-PST] gives 11 and 16 bits for the other formats). In every OST read
so far each heap ID names block 0 and an index below 2,048, where the two layouts agree,
so the 4 KB split itself has been checked only against the tests' own files.

"The tests' own files" are built in memory by `tests/pstbuilder.py`, which is written
from the same layouts as the reader. They show the reader and that writer agree. That
the reader agrees with Outlook rests on the real files above.

## Command line

```
python3 pstprobe.py info     mailbox.pst      # format, encoding, node counts by type
python3 pstprobe.py folders  mailbox.pst      # every folder: NID, item count, class, path
python3 pstprobe.py items    mailbox.pst      # one JSON line per item
python3 pstprobe.py items    --subjects mailbox.pst
python3 pstprobe.py props    mailbox.pst 0x200044   # every property of one node
python3 pstprobe.py body     mailbox.pst 0x200044 --format rtf -o body.rtf
python3 pstprobe.py body     mailbox.pst 0x200044 --format from-rtf -o body.txt
python3 pstprobe.py extract  mailbox.pst -o attachments/
python3 pstprobe.py export   mailbox.pst --folder 0x8082 -o out/      # one folder
python3 pstprobe.py export   mailbox.pst --recursive -o out/          # every folder
python3 pstprobe.py verify   mailbox.pst      # the stored CRCs of header, pages and blocks
```

`verify` recomputes the CRCs the file stores ([MS-PST] 5.3) and compares them: the header's
(dwCRCPartial and, in a Unicode file, dwCRCFull), every page of the two B-trees, and every
block, as stored (before decoding or inflating). It prints JSON counts of matches and
mismatches, with up to ten page offsets or BIDs of each failure, and exits 1 if anything
does not match or could not be read. A page or block that cannot be read is counted as
"unreadable", apart from mismatches. Allocation map pages and the density list are not
read by pstprobe and are not checked. `verify` reads every block, so it takes as long as
reading the whole file. The other commands do not check these CRCs and read a file whose
CRCs do not match. (The CRC inside a compressed RTF body is a different check, made
whenever the body is read.)

`body` writes one item's body: `text` (PidTagBody, as UTF-8), `html` (PidTagBodyHtml as
stored), `rtf` (PidTagRtfCompressed, decompressed) or `from-rtf` (the plain text or HTML that
the RTF encapsulates, or else the text of the RTF document, below). It writes to standard
output, or with
`-o` to a file that must not exist yet.

`items` gives each item's folder, NID, message class, recipient count and its
attachments (method, PidTagAttachSize, and the size of the attached data, or, for an
attached item, the same summary of that item).

`export` writes, for the folder (and with `--recursive` each folder below it, in a
folder of its own named from its display name and NID): `folder.json` with the folder's
properties, `items.jsonl` with one line per item (every property with its
[MS-OXPROPS] name, type and value, the recipients, and the attachments, attached items
included), each compressed RTF body decompressed under `bodies/` as `<item NID>.rtf` (named
in the item's line as `rtf_file`) with what `from-rtf` gives beside it as
`<item NID>.from-rtf.txt` or `.from-rtf.html` (`rtf_original_file`, and `rtf_original_kind`:
`text`, `html` or `rendered`). For RTF that is not HTML, `rtf_attachment_positions` gives
where each attachment belongs in that text (below). The file attachments go under
`attachments/`, named
`<item NID>_<attachment NID>_<stored name>`. An attachment of an attached item adds the
attached item's attachment NID to the chain (`<item NID>_<NID>_<NID>_<stored name>`),
because attachment NIDs are local to the item that holds them. A file that already exists
is never replaced: the write stops with an error, and an export into a folder that already
holds an export (its `items.jsonl`) is refused before anything is written. A stored name
is cut to 180 bytes (UTF-8), keeping a short extension. A damaged item gets a line with
its `error` and the export goes on; a damaged folder is counted under `errors` and named
on standard error; a folder that a hierarchy table lists a second time is exported once,
and the command says how many there were. (`export_folder` returns the same as
`folder_errors` and `folders_repeated`.) `extract` writes every file attachment of the whole
file, those of attached items included, with the same names; it goes past a damaged item
(naming it on standard error) and exits 1 if there was one. Output on standard output and
standard error is UTF-8.

Property names come from `pstprobe_names.py`, generated from [MS-OXPROPS] v20250520 by
`tools/make_names.py` (545 PidTag and 365 PidLid names). Without that file beside it,
`pstprobe.py` still reads everything and shows property IDs only.

## In a window

```
python3 pstprobe_gui.py [mailbox.pst]
```

`pstprobe_gui.py` is an optional tkinter window, standard library only, that needs
`pstprobe.py` and `pstprobe_theme.py` beside it (its logo is read from `assets/` and left out
when that folder is missing). A switch in its header, or View > Dark Mode, picks the light or
dark look; until one is chosen it follows the system's setting where it can read it
(macOS, Windows, and GNOME's setting on Linux), otherwise light. On the left is the folder tree with
each folder's item count. On the right are the selected folder's items, 500 to a page,
with their delivery and modification times (UTC) first. Below them, tabs show the
selected item's properties with their names, its recipients, its attachments and its
body: the plain text, the HTML source, the RTF source (decompressed) or the text of the RTF
(what `from-rtf` gives, with a mark such as `[pstprobe: attachment 0x8025 report.pdf]` where
each attachment belongs; the marks are the window's own and Save Body writes the text
without them), whichever the item has, shown as text and never rendered (HTML decoded by the item's PidTagInternetCodepage
when Python knows it, else as UTF-8, else as cp1252). NUL characters are left out of the shown text
(Tk drops them); Save Body writes the stored bytes. It saves a body, one attachment, all
of an item's attachments, or exports a
folder (with or without its subfolders) using the same code as `extract` and `export`.
The file is opened read-only; the window writes nothing but its size and colour mode,
in the user's settings folder. On a Mac, run it with a Python that ships Tk 8.6 (3.13 or
earlier from python.org): Tk 9, in python.org's 3.14, has aborted tkinter windows in
testing. The binaries bundle Python 3.13 with Tk 8.6.

## How values are returned

- Times (PtypTime) are timezone-aware `datetime` objects in UTC. A zero time is `None`.
- Strings end at their terminating zero ([MS-OXCDATA] 2.11.1). In ANSI files Outlook
  has been seen to store other bytes after the terminator; `PropertyContext.raw()`
  returns the stored bytes, including those.
- A PtypString value with an odd number of bytes cannot be UTF-16, so it is returned as
  the stored `bytes`. Outlook has also been seen to store 8-bit text of even length under
  the PtypString type (a search folder's container class, in the Outlook PST above).
  That case cannot be told apart from real UTF-16 and decodes as unreadable text.
- 8-bit strings (PtypString8, the strings of an ANSI file) are decoded with the item's
  PidTagMessageCodepage, the code page [MS-OXPROPS] 2.790 gives for its non-Unicode
  string properties; an item's recipients and attachments use the item's. A folder, the
  store, and an item that names no code page use the file's, which [MS-PST] does not
  record: Windows-1252 unless `--codepage N` (or `open_pst(path, codepage=N)`) says
  otherwise. A file written on a system with another code page therefore shows wrong
  characters in those names until the right one is given. The window always uses 1252
  there. A PtypString8 PidTagBody is decoded with PidTagInternetCodepage, the body's code
  page (2.747), when the item has one and the body decodes in it; otherwise as the item's
  other 8-bit strings. Bytes a code page does not define become U+FFFD. Only Western (1252) ANSI files have been read.
- `Message.subject` drops the two-character prefix marker (SOH and a length) that a
  stored subject can start with. `get(0x0037)` returns the subject as stored.
- Named properties (IDs from 0x8000) are resolved through the store's name-to-ID map:
  `message.get_named(guid, lid_or_name)`.
- A search folder's results name items that live in other folders. They are not
  returned by `messages()`; `search_result_nids()` lists them. Some files keep a search
  folder's results in a table whose heap has client signature 0xAC, which [MS-PST] 2.3.1.2
  lists as reserved. For that table only the row index is read (the item NIDs, from the
  layout in the PFF notes below); its rows' values are not, and `search_contents_table()`
  raises `UnsupportedError` for it. In the three files seen with one, every row was an item
  that a normal folder also lists, and the count equalled the folder's
  PidTagContentCount.
- `info` reports PidTagPstPassword, the CRC-32 of a PST password ([MS-PST] 2.4.3.3), as
  `password_crc32`, and `password_set` is true when it is nonzero. The password does not
  protect the data, so the file is read either way: the specification says readers must
  enforce it, and pstprobe reports it instead. Every OST read so far (five) stores
  0xFFFFFFFF; what that means in an OST is not established, so `password_set` is null for
  an OST. The window shows the same in its banner.
- A structure that does not match the format raises `CorruptError` with the offset or
  node involved. `items` prints such an error on that item's line and goes on.

## What it does not do yet

- The text of an RTF document is its characters only. Formatting is not rendered: Outlook's
  own plain text adds ">" before quoted lines and "*" before list items, and pstprobe's
  does not. Embedded objects and pictures are skipped.
- An attachment that the counts of [MS-OXRTFEX] 2.2.3.4 send to the end is not placed by
  its PidTagRenderingPosition (which the specification allows but does not require).
- No recovery. Items that are no longer linked from a folder, and data in free space,
  are not looked for. `info` counts every node in the node B-tree by type, so a
  difference from the items the folders list is visible. In the Outlook PST above, a
  mail deleted from Deleted Items was not among the file's message nodes; whether any
  of it remains elsewhere in the file was not examined.
- CRCs are checked only by `verify`, not while reading.
- Personal address books (`.pab`) are accepted by signature and have not been tested.

## Binaries

`.github/workflows/build.yml` freezes the command line and the window with PyInstaller for
Windows, macOS and Linux, x64 and arm64, and checks each build against the source with
`tools/smoke_binary.py`: the same output for `info`, `folders`, `items`, `props`, `verify`
and `body`, the same files from `export`, and the window's self-check. The builds of a
release are attached to it after that check and after a run on real PST and OST files,
and the release's `SHA256SUMS.txt` lists every file attached to it. Nothing to install.
The builds are not code signed: on Windows, SmartScreen may stop them (More info, then
Run anyway); on macOS, allow them in System Settings, Privacy & Security, or run
`xattr -dr com.apple.quarantine` on the file or the unzipped `.app`. On macOS and Linux,
a downloaded command-line file needs `chmod +x` before it runs (and so does the Linux
window). The window builds include Tcl and Tk, whose licence terms come with them
(`licenses/`).

## Validation

`tools/compare_libpff.py` compares this reader with libpff (libpff-python 20261030),
run as a comparison oracle in a separate environment by `tools/oracle_dump.py`. On the
four real files below it reports no difference in folders, items per folder, recipients per
item, attachments per item, or the data sizes of file attachments (attached items are
compared by count only):

| File | Folders | Items | Recipients | Attachments |
|---|---|---|---|---|
| CFReDS Data Leakage OST (corpus key `cfreds_leakage_win7`) | 49 | 14 | 14 | 0 |
| Outlook Unicode PST (corpus key `windows11_arm_outlook_pst_known_20261109`) | 24 | 19 | 6 | 2 |
| Outlook ANSI PST (same corpus key) | 7 | 3 | 3 | 1 |
| Outlook Unicode PST, larger (corpus key `windows11_arm_outlook_pst_stress_known_20261109`) | 24 | 1,222 | 6 | 353 |

The larger PST (14.5 MB) is there for structure: `tools/structure_census.py` finds in it
three-level node and block B-trees, a BTH with an index level, heaps and table row
matrices that span blocks, XBLOCK and XXBLOCK data trees (a 10,000,000-byte attachment)
and an SIBLOCK subnode tree (an item with 350 attachments). The other three files hold
none of these. `items` reads all 1,222 items of it in about 0.1 s with a peak resident
size of 32 MB on an Apple M2 Max.

The Enron EDRM v2 set in PST form (171 Unicode PSTs, 83 GB, the modified release by
Intellekt on Hugging Face; corpus key `enron_edrm_v2_pst_hf_20261109`) was read in full:

- `tools/read_all.py` read all 1,226,178 items, 6,478,537 recipients and 453,832
  attachments (64 GB of attachment data) with no read failure. Those item and attachment
  totals equal the counts the release publishes. Every folder's item count equals its
  PidTagContentCount, every item's recipient count equals the names in its display lists,
  and every attachment read back its full data size. The largest file is 2.1 GB and took
  41 s.
- libpff could not read 26 of the files (it stops at a folder with a local descriptor size
  error). On the other 145, `tools/compare_libpff.py` reports no difference. In those
  files libpff could not open 12,912 items (all of which pstprobe read, with recipient
  counts that match their display lists) and could not read the attachments of 1,212
  more; those are not compared with libpff.

`tools/known-data/check_known.py` checks the two Outlook PSTs against what was done to
make them: the item counts per folder and class that Outlook reported, the SHA-256 of
the attached file, the attached item, and the three appointments' start times. With
`--stress` it checks the larger PST: 1,207 items in Drafts, the 10 MB attachment's
SHA-256 and the item with 350 attachments.

`tools/read_all.py` needs no other reader: it reads every item, recipient and attached
file of a PST or OST and checks each against a second record in the file itself (the
folder's PidTagContentCount, the attachment's data size and PidTagAttachSize, the item's
display lists). It prints counts only.

Compressed RTF is checked by its own CRC, which `decompress_rtf` verifies on every body as
[MS-OXRTFCP] requires. Every compressed RTF body read so far passed it and decompressed to
exactly its header's RAWSIZE: 539 in the Enron set, 526 in 19 distinct public test files
from five PST libraries (corpus key `pst_public_fixtures_20261109`), 8 in the two Unicode
Outlook PSTs and 3 in the CFReDS OST (attached items included). The ANSI Outlook PST holds
none. The tests include
the specification's two worked examples. (Its second example prints the result as
`{\ref1 ...`; the compressed bytes can only give `{\rtf1 ...`, and the CRC matches.)

An RTF body that encapsulates plain text (`\fromtext`) or HTML (`\fromhtml1`) is
de-encapsulated by `deencapsulate_rtf`, following [MS-OXRTFEX] 2.2.3 over the RTF syntax of
the RTF 1.9.1 specification: recognition in the first 10 tokens, HTMLTAG content, HTMLRTF
suppression scoped by group, destinations skipped (the list is the 294 control words the
RTF specification's index types as destinations), text decoded by the current font's code
page, surrogate pairs in `\uN` joined (an unpaired half becomes U+FFFD), and a backslash
before CR or LF read as `\par`. `tools/rtf_body_check.py` compares each de-encapsulated
body with the item's own PidTagBody, a second record of the same content:

- Plain text, against a second record: in the Enron set, all 62 RTF bodies that encapsulate
  plain text de-encapsulate to exactly the item's own PidTagBody (CRLF compared as LF, and
  trailing line breaks, spaces and NULs set aside).
- HTML: all 17 HTML-encapsulating bodies read so far (16 in the Enron set, 1 in the public
  test files) give visible text (by `html.parser`, without head, style and script) with the
  same non-whitespace characters as PidTagBody, after removing from both the bracketed link
  targets (`<http://...>`, `<name@host>`) that a plain-text body adds for each link. With
  `\htmlrtf` suppression switched off in a test copy, 9 of the 17 differ, so the comparison
  catches that fault. None of them stores PidTagBodyHtml, so the HTML markup itself is
  checked only by the specification's worked example (3.1), which comes out as its RTF says.
- RTF documents (RTF that encapsulates nothing), whose text `render_rtf_text` gives by the
  same rules: of 495 that also store PidTagBody (Enron and the public test files), 439
  have the same non-whitespace characters, and the other 56 differ only by the ">" and "*"
  marks Outlook adds for quotes and lists (`rtf_body_check.py` counts these apart).
- In the public test files, the 488 plain-text-encapsulating bodies are all one short
  template that holds no text after its header and font table, and de-encapsulate to empty
  text.

Attachment placement ([MS-OXRTFEX] 2.2.3.4): `\objattph` appears only in RTF documents in
the files read so far (117 items: 115 in the Enron set, 2 in the public test files), never
in RTF that encapsulates text or HTML. In 69 of them the number of `\objattph` equals the
number of attachments that are not hidden and have a rendering position, and all 81 of
those attachments are placed; each lands on the placeholder character (`\'20`) that
follows its `\objattph`. In the other 48 the numbers differ, so their attachments (8, in
4 items) go at the end, as the specification says. The other 44 have no attachments at
all; this Enron release says it removed 39,147 attachments, which may be why, but that is
not established item by item. No second record of where Outlook would place them was found (its
plain text has no placeholder), so placement is checked against the RTF itself.

`tools/gui_exercise.py` drives the window itself, with Tk's event loop running: it opens
each file, expands every folder, shows every page, checks the tabs of a sample of items
against the reader, switches bodies, sorts, copies, saves and exports (comparing the
files with `export_folder`), refuses a second save to the same name, cancels a task,
refuses a second export into the same folder (leaving the first byte for byte as it was),
and opens a damaged copy and a file that is not a PST. Cancel is tried while the export is
held at its first read, so it is reached on every file that has items. On 24 files (23
distinct: the Outlook PSTs, the CFReDS OST and the public test files) it ran 5,288 checks
with no failure on each of macOS 27 (Python 3.12, Tk 8.6), Windows 11 on ARM (Python 3.11,
Tk 8.6) and Ubuntu 26.04 (Python 3.14, Tk 8.6).

`verify` was run on every file above. All stored CRCs matched: in the Enron set, the
headers of all 171 files, 1,049,369 B-tree pages and 14,152,495 blocks (331 s in all, at
most 10.4 s for one file); in the 23 distinct other files (the Outlook PSTs, the CFReDS
OST and the public test files), 1,412 pages and 14,750 blocks. To show it can fail, one
byte was flipped in a copy of a public test file, once in a block, once in a B-tree page
and once in the header: each time `verify` named that block, that page or the header,
and exited 1. Those files hold no mismatch, so how `verify` reports one in a real damaged
file has been seen only on those planted copies.

The tests (`python3 -m unittest discover -s tests`) need no data and no packages.

## Sources

- Microsoft, [MS-PST] "Outlook Personal Folders (.pst) File Format", v20250218
  (revision 11.2): the header, B-trees, blocks, both encodings, the heap, BTH, property
  and table contexts, and the messaging layer.
- Joachim Metz, "Personal Folder File (PFF) file format specification", in
  libyal/libpff under `documentation/` (GNU Free Documentation License): the layout of
  the 4 KB-page OST format, which [MS-PST] does not describe. It was read for the
  structure only; no text is copied. Two points were measured on a real OST instead:
  a block's 24-byte trailer sits at the end of its 512-byte-aligned allocation, and a
  compressed block is a zlib stream (header 0x78 0x9C), not raw deflate. A block can
  inflate past 8,192 bytes (26,644 bytes seen). The same notes give the layout of the
  0xAC table's header and row index, and the place of a 4 KB page's CRC; both were
  checked on real files (above, and every 4 KB page CRC in the OSTs read matched).
- Microsoft, [MS-OXCDATA] v20250520, 2.11.1, for string termination, and [MS-OXPROPS]
  for property IDs.
- Microsoft, [MS-OXRTFCP] v20250520, for compressed RTF: the header, the 207-byte
  dictionary, runs and references, and the CRC.
- Microsoft, [MS-OXRTFEX] v20250520, 2.1.3.1 and 2.2.3, for de-encapsulation, and Microsoft,
  "Rich Text Format (RTF) Specification", version 1.9.1 (2008), for RTF syntax, `\uN` and
  `\ucN`, `\binN`, the `\fcharsetN` code page table and the index of destinations.

## License

MIT. See `LICENSE`.
