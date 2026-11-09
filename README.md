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
python3 pstprobe.py extract  mailbox.pst -o attachments/
python3 pstprobe.py export   mailbox.pst --folder 0x8082 -o out/      # one folder
python3 pstprobe.py export   mailbox.pst --recursive -o out/          # every folder
```

`body` writes one item's body: `text` (PidTagBody, as UTF-8), `html` (PidTagBodyHtml as
stored) or `rtf` (PidTagRtfCompressed, decompressed). It writes to standard output, or with
`-o` to a file that must not exist yet.

`items` gives each item's folder, NID, message class, recipient count and its
attachments (method, PidTagAttachSize, and the size of the attached data, or, for an
attached item, the same summary of that item).

`export` writes, for the folder (and with `--recursive` each folder below it, in a
folder of its own named from its display name and NID): `folder.json` with the folder's
properties, `items.jsonl` with one line per item (every property with its
[MS-OXPROPS] name, type and value, the recipients, and the attachments, attached items
included), each compressed RTF body decompressed under `bodies/` as `<item NID>.rtf` (named
in the item's line as `rtf_file`), and the file attachments under `attachments/`, named
`<item NID>_<attachment NID>_<stored name>`. An attachment of an attached item adds the
attached item's attachment NID to the chain (`<item NID>_<NID>_<NID>_<stored name>`),
because attachment NIDs are local to the item that holds them. A file that already exists
is never replaced: the write stops with an error. `extract` writes every file attachment
of the whole file, those of attached items included, with the same names.

Property names come from `pstprobe_names.py`, generated from [MS-OXPROPS] v20250520 by
`tools/make_names.py` (545 PidTag and 365 PidLid names). Without that file beside it,
`pstprobe.py` still reads everything and shows property IDs only.

## In a window

```
python3 pstprobe_gui.py [mailbox.pst]
```

`pstprobe_gui.py` is an optional tkinter window, standard library only, that needs
`pstprobe.py` and `pstprobe_theme.py` beside it. On the left is the folder tree with
each folder's item count. On the right are the selected folder's items, 500 to a page,
with their delivery and modification times (UTC) first. Below them, tabs show the
selected item's properties with their names, its recipients, its attachments and its
body: the plain text, the HTML source or the RTF source (decompressed), whichever the item
stores, shown as text and never rendered. NUL characters are left out of the shown text
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
- `Message.subject` drops the two-character prefix marker (SOH and a length) that a
  stored subject can start with. `get(0x0037)` returns the subject as stored.
- Named properties (IDs from 0x8000) are resolved through the store's name-to-ID map:
  `message.get_named(guid, lid_or_name)`.
- A search folder's results name items that live in other folders. They are not
  returned by `messages()`; `search_result_nids()` lists them.
- `info` reports PidTagPstPassword, the CRC-32 of a PST password ([MS-PST] 2.4.3.3), as
  `password_crc32`, and `password_set` is true when it is nonzero. The password does not
  protect the data, so the file is read either way: the specification says readers must
  enforce it, and pstprobe reports it instead. Every OST read so far (five) stores
  0xFFFFFFFF; what that means in an OST is not established, so `password_set` is null for
  an OST. The window shows the same in its banner.
- A structure that does not match the format raises `CorruptError` with the offset or
  node involved. `items` prints such an error on that item's line and goes on.

## What it does not do yet

- RTF bodies are decompressed, not converted. An RTF body that wraps plain text or HTML
  (`\fromtext`, `\fromhtml1`, [MS-OXRTFEX]) is returned as RTF; the wrapped text is not
  extracted from it.
- No recovery. Items that are no longer linked from a folder, and data in free space,
  are not looked for. `info` counts every node in the node B-tree by type, so a
  difference from the items the folders list is visible. In the Outlook PST above, a
  mail deleted from Deleted Items was not among the file's message nodes; whether any
  of it remains elsewhere in the file was not examined.
- Page and block CRCs are not checked.
- Personal address books (`.pab`) are accepted by signature and have not been tested.

## Binaries

Each release has unsigned builds of the command line and the window for Windows, macOS
and Linux, x64 and arm64, made by `.github/workflows/build.yml` with PyInstaller. Nothing
to install. They are not code signed: on Windows, SmartScreen may stop them (More info,
then Run anyway); on macOS, allow them in System Settings, Privacy & Security, or run
`xattr -dr com.apple.quarantine` on the file or the unzipped `.app`. On Linux, mark the
file executable first. Each build is checked in CI against the source by
`tools/smoke_binary.py`: the same output for `info`, `folders`, `items` and `props`, the
same files from `export`, and the window's self-check.

## Validation

`tools/compare_libpff.py` compares this reader with libpff (libpff-python 20261030),
run as a comparison oracle in a separate environment by `tools/oracle_dump.py`. On the
three real files it reports no difference in folders, items per folder, recipients per
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

`tools/gui_exercise.py` drives the window itself, with Tk's event loop running: it opens
each file, expands every folder, shows every page, checks the tabs of a sample of items
against the reader, switches bodies, sorts, copies, saves and exports (comparing the
files with `export_folder`), refuses a second save to the same name, cancels a task, and
opens a damaged copy and a file that is not a PST. On 24 files (the Outlook PSTs, the
CFReDS OST and the public test files) it ran 4,673 checks with no failure on macOS 27
(Python 3.12, Tk 8.6), Windows 11 on ARM (Python 3.11, Tk 8.6) and Ubuntu 26.04 (Python
3.14, Tk 8.6).

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
  inflate past 8,192 bytes (26,644 bytes seen).
- Microsoft, [MS-OXCDATA] v20250520, 2.11.1, for string termination, and [MS-OXPROPS]
  for property IDs.
- Microsoft, [MS-OXRTFCP] v20250520, for compressed RTF: the header, the 207-byte
  dictionary, runs and references, and the CRC.

## License

MIT. See `LICENSE`.
