"""pstprobe: a read-only reader for Outlook PST and OST files.

One file, pure Python, standard library only. It opens a Personal Storage Table
(.pst) or an Offline Storage Table (.ost) and reads its folders, the items in
them (mail, contacts, appointments, tasks, notes, journal entries and the rest),
their recipients and their attachments, including attached items.

    import pstprobe

    with pstprobe.open_pst("mailbox.pst") as pst:
        for folder in pst.walk_folders():
            for message in folder.messages():
                print(folder.path, message.message_class, message.subject)

The file is read through seek and read only, a node at a time, so a file of
several gigabytes is never loaded whole.

Written from Microsoft's published specification, [MS-PST] "Outlook Personal
Folders (.pst) File Format" (v20250218, revision 11.2). The 4 KB page variant
that Outlook 2013 and later write for OST files (wVer 36) is not in [MS-PST];
its page, node and block layouts follow Joachim Metz, "Personal Folder File
(PFF) file format specification", in the libyal/libpff repository under
``documentation/``, checked against a real OST. No code is taken from libpff,
which is LGPL, or from any other PST implementation. This file is MIT.
"""

import argparse
import datetime
import json
import os
import struct
import sys
import uuid
import zlib
from collections import OrderedDict

__version__ = "0.1.0"

__all__ = [
    "open_pst", "PSTFile", "Folder", "Message", "Attachment", "Node",
    "PSTError", "CorruptError", "UnsupportedError", "__version__",
]


class PSTError(Exception):
    """Base error for this module."""


class CorruptError(PSTError):
    """A structure in the file does not match the format."""


class UnsupportedError(PSTError):
    """The file uses a variant this reader does not read."""


# ---------------------------------------------------------------------------
# NDB_CRYPT_PERMUTE and NDB_CRYPT_CYCLIC tables, [MS-PST] section 5.1.
# mpbbR is bytes 0-255, mpbbS 256-511, mpbbI 512-767. mpbbI is the inverse
# permutation of mpbbR; the module checks this at import.

_MPBB_CRYPT = bytes([
     65,  54,  19,  98, 168,  33, 110, 187, 244,  22, 204,   4, 127, 100, 232,  93,
     30, 242, 203,  42, 116, 197,  94,  53, 210, 149,  71, 158, 150,  45, 154, 136,
     76, 125, 132,  63, 219, 172,  49, 182,  72,  95, 246, 196, 216,  57, 139, 231,
     35,  59,  56, 142, 200, 193, 223,  37, 177,  32, 165,  70,  96,  78, 156, 251,
    170, 211,  86,  81,  69, 124,  85,   0,   7, 201,  43, 157, 133, 155,   9, 160,
    143, 173, 179,  15,  99, 171, 137,  75, 215, 167,  21,  90, 113, 102,  66, 191,
     38,  74, 107, 152, 250, 234, 119,  83, 178, 112,   5,  44, 253,  89,  58, 134,
    126, 206,   6, 235, 130, 120,  87, 199, 141,  67, 175, 180,  28, 212,  91, 205,
    226, 233,  39,  79, 195,   8, 114, 128, 207, 176, 239, 245,  40, 109, 190,  48,
     77,  52, 146, 213,  14,  60,  34,  50, 229, 228, 249, 159, 194, 209,  10, 129,
     18, 225, 238, 145, 131, 118, 227, 151, 230,  97, 138,  23, 121, 164, 183, 220,
    144, 122,  92, 140,   2, 166, 202, 105, 222,  80,  26,  17, 147, 185,  82, 135,
     88, 252, 237,  29,  55,  73,  27, 106, 224,  41,  51, 153, 189, 108, 217, 148,
    243,  64,  84, 111, 240, 198, 115, 184, 214,  62, 101,  24,  68,  31, 221, 103,
     16, 241,  12,  25, 236, 174,   3, 161,  20, 123, 169,  11, 255, 248, 163, 192,
    162,   1, 247,  46, 188,  36, 104, 117,  13, 254, 186,  47, 181, 208, 218,  61,
     20,  83,  15,  86, 179, 200, 122, 156, 235, 101,  72,  23,  22,  21, 159,   2,
    204,  84, 124, 131,   0,  13,  12,  11, 162,  98, 168, 118, 219, 217, 237, 199,
    197, 164, 220, 172, 133, 116, 214, 208, 167, 155, 174, 154, 150, 113, 102, 195,
     99, 153, 184, 221, 115, 146, 142, 132, 125, 165,  94, 209,  93, 147, 177,  87,
     81,  80, 128, 137,  82, 148,  79,  78,  10, 107, 188, 141, 127, 110,  71,  70,
     65,  64,  68,   1,  17, 203,   3,  63, 247, 244, 225, 169, 143,  60,  58, 249,
    251, 240,  25,  48, 130,   9,  46, 201, 157, 160, 134,  73, 238, 111,  77, 109,
    196,  45, 129,  52,  37, 135,  27, 136, 170, 252,   6, 161,  18,  56, 253,  76,
     66, 114, 100,  19,  55,  36, 106, 117, 119,  67, 255, 230, 180,  75,  54,  92,
    228, 216,  53,  61,  69, 185,  44, 236, 183,  49,  43,  41,   7, 104, 163,  14,
    105, 123,  24, 158,  33,  57, 190,  40,  26,  91, 120, 245,  35, 202,  42, 176,
    175,  62, 254,   4, 140, 231, 229, 152,  50, 149, 211, 246,  74, 232, 166, 234,
    233, 243, 213,  47, 112,  32, 242,  31,   5, 103, 173,  85,  16, 206, 205, 227,
     39,  59, 218, 186, 215, 194,  38, 212, 145,  29, 210,  28,  34,  51, 248, 250,
    241,  90, 239, 207, 144, 182, 139, 181, 189, 192, 191,   8, 151,  30, 108, 226,
     97, 224, 198, 193,  89, 171, 187,  88, 222,  95, 223,  96, 121, 126, 178, 138,
     71, 241, 180, 230,  11, 106, 114,  72, 133,  78, 158, 235, 226, 248, 148,  83,
    224, 187, 160,   2, 232,  90,   9, 171, 219, 227, 186, 198, 124, 195,  16, 221,
     57,   5, 150,  48, 245,  55,  96, 130, 140, 201,  19,  74, 107,  29, 243, 251,
    143,  38, 151, 202, 145,  23,   1, 196,  50,  45, 110,  49, 149, 255, 217,  35,
    209,   0,  94, 121, 220,  68,  59,  26,  40, 197,  97,  87,  32, 144,  61, 131,
    185,  67, 190, 103, 210,  70,  66, 118, 192, 109,  91, 126, 178,  15,  22,  41,
     60, 169,   3,  84,  13, 218,  93, 223, 246, 183, 199,  98, 205, 141,   6, 211,
    105,  92, 134, 214,  20, 247, 165, 102, 117, 172, 177, 233,  69,  33, 112,  12,
    135, 159, 116, 164,  34,  76, 111, 191,  31,  86, 170,  46, 179, 120,  51,  80,
    176, 163, 146, 188, 207,  25,  28, 167,  99, 203,  30,  77,  62,  75,  27, 155,
     79, 231, 240, 238, 173,  58, 181,  89,   4, 234,  64,  85,  37,  81, 229, 122,
    137,  56, 104,  82, 123, 252,  39, 174, 215, 189, 250,   7, 244, 204, 142,  95,
    239,  53, 156, 132,  43,  21, 213, 119,  52,  73, 182,  18,  10, 127, 113, 136,
    253, 157,  24,  65, 125, 147, 216,  88,  44, 206, 254,  36, 175, 222, 184,  54,
    200, 161, 128, 166, 153, 152, 168,  47,  14, 129, 101, 115, 228, 194, 162, 138,
    212, 225,  17, 208,   8, 139,  42, 242, 237, 154, 100,  63, 193, 108, 249, 236,
])
_MPBB_R = _MPBB_CRYPT[0:256]
_MPBB_S = _MPBB_CRYPT[256:512]
_MPBB_I = _MPBB_CRYPT[512:768]
if any(_MPBB_I[_MPBB_R[i]] != i for i in range(256)):  # pragma: no cover
    raise ImportError("permute table is damaged")


def crypt_permute(data, encrypt=False):
    """NDB_CRYPT_PERMUTE ([MS-PST] 5.1): one table lookup per byte."""
    return bytes(data).translate(_MPBB_R if encrypt else _MPBB_I)


def crypt_cyclic(data, key):
    """NDB_CRYPT_CYCLIC ([MS-PST] 5.2). Symmetric. key is the low 32 bits of the BID."""
    key &= 0xFFFFFFFF
    w = (key ^ (key >> 16)) & 0xFFFF
    out = bytearray(data)
    r, s, i_ = _MPBB_R, _MPBB_S, _MPBB_I
    for n, b in enumerate(out):
        lo, hi = w & 0xFF, (w >> 8) & 0xFF
        b = (b + lo) & 0xFF
        b = r[b]
        b = (b + hi) & 0xFF
        b = s[b]
        b = (b - hi) & 0xFF
        b = i_[b]
        b = (b - lo) & 0xFF
        out[n] = b
        w = (w + 1) & 0xFFFF
    return bytes(out)


# ---------------------------------------------------------------------------
# Constants

CRYPT_NONE = 0x00
CRYPT_PERMUTE = 0x01
CRYPT_CYCLIC = 0x02
CRYPT_EDPCRYPTED = 0x10
CRYPT_NAMES = {0x00: "none", 0x01: "permute", 0x02: "cyclic", 0x10: "edpcrypted (WIP)"}

PTYPE_BBT = 0x80
PTYPE_NBT = 0x81

# NID types, [MS-PST] 2.2.2.1
NID_TYPE_HID = 0x00
NID_TYPE_INTERNAL = 0x01
NID_TYPE_NORMAL_FOLDER = 0x02
NID_TYPE_SEARCH_FOLDER = 0x03
NID_TYPE_NORMAL_MESSAGE = 0x04
NID_TYPE_ATTACHMENT = 0x05
NID_TYPE_SEARCH_UPDATE_QUEUE = 0x06
NID_TYPE_SEARCH_CRITERIA_OBJECT = 0x07
NID_TYPE_ASSOC_MESSAGE = 0x08
NID_TYPE_CONTENTS_TABLE_INDEX = 0x0A
NID_TYPE_RECEIVE_FOLDER_TABLE = 0x0B
NID_TYPE_OUTGOING_QUEUE_TABLE = 0x0C
NID_TYPE_HIERARCHY_TABLE = 0x0D
NID_TYPE_CONTENTS_TABLE = 0x0E
NID_TYPE_ASSOC_CONTENTS_TABLE = 0x0F
NID_TYPE_SEARCH_CONTENTS_TABLE = 0x10
NID_TYPE_ATTACHMENT_TABLE = 0x11
NID_TYPE_RECIPIENT_TABLE = 0x12
NID_TYPE_SEARCH_TABLE_INDEX = 0x13
NID_TYPE_LTP = 0x1F

NID_TYPE_NAMES = {
    0x00: "HID", 0x01: "INTERNAL", 0x02: "NORMAL_FOLDER", 0x03: "SEARCH_FOLDER",
    0x04: "NORMAL_MESSAGE", 0x05: "ATTACHMENT", 0x06: "SEARCH_UPDATE_QUEUE",
    0x07: "SEARCH_CRITERIA_OBJECT", 0x08: "ASSOC_MESSAGE", 0x0A: "CONTENTS_TABLE_INDEX",
    0x0B: "RECEIVE_FOLDER_TABLE", 0x0C: "OUTGOING_QUEUE_TABLE", 0x0D: "HIERARCHY_TABLE",
    0x0E: "CONTENTS_TABLE", 0x0F: "ASSOC_CONTENTS_TABLE", 0x10: "SEARCH_CONTENTS_TABLE",
    0x11: "ATTACHMENT_TABLE", 0x12: "RECIPIENT_TABLE", 0x13: "SEARCH_TABLE_INDEX",
    0x1F: "LTP",
}

# Special NIDs, [MS-PST] 2.4.1
NID_MESSAGE_STORE = 0x21
NID_NAME_TO_ID_MAP = 0x61
NID_ROOT_FOLDER = 0x122
NID_ATTACHMENT_TABLE = 0x671
NID_RECIPIENT_TABLE = 0x692

# Heap-on-node client signatures, [MS-PST] 2.3.1.2
HN_SIG = 0xEC
CLIENT_SIG_TC = 0x7C
CLIENT_SIG_BTH = 0xB5
CLIENT_SIG_PC = 0xBC

# Property types, [MS-PST] 2.1.1
PT_INT16 = 0x0002
PT_INT32 = 0x0003
PT_FLOAT = 0x0004
PT_DOUBLE = 0x0005
PT_CURRENCY = 0x0006
PT_APPTIME = 0x0007
PT_ERROR = 0x000A
PT_BOOLEAN = 0x000B
PT_OBJECT = 0x000D
PT_INT64 = 0x0014
PT_STRING8 = 0x001E
PT_UNICODE = 0x001F
PT_SYSTIME = 0x0040
PT_GUID = 0x0048
PT_SERVERID = 0x00FB
PT_RESTRICTION = 0x00FD
PT_RULEACTION = 0x00FE
PT_BINARY = 0x0102
PT_MV = 0x1000

_FIXED_SIZE = {
    PT_INT16: 2, PT_INT32: 4, PT_FLOAT: 4, PT_DOUBLE: 8, PT_CURRENCY: 8,
    PT_APPTIME: 8, PT_ERROR: 4, PT_BOOLEAN: 1, PT_INT64: 8, PT_SYSTIME: 8,
    PT_GUID: 16,
}
# In a PC, a fixed-size value of four bytes or fewer is stored in dwValueHnid
# itself ([MS-PST] 2.3.3.3).
_PC_INLINE = {PT_INT16, PT_INT32, PT_FLOAT, PT_ERROR, PT_BOOLEAN}

PS_MAPI = uuid.UUID("00020328-0000-0000-c000-000000000046")
PS_PUBLIC_STRINGS = uuid.UUID("00020329-0000-0000-c000-000000000046")

# Property identifiers used by the messaging helpers ([MS-OXPROPS]).
PID_TAG_DISPLAY_NAME = 0x3001
PID_TAG_CONTENT_COUNT = 0x3602
PID_TAG_CONTENT_UNREAD_COUNT = 0x3603
PID_TAG_SUBFOLDERS = 0x360A
PID_TAG_CONTAINER_CLASS = 0x3613
PID_TAG_MESSAGE_CLASS = 0x001A
PID_TAG_SUBJECT = 0x0037
PID_TAG_MESSAGE_CODEPAGE = 0x3FFD
PID_TAG_INTERNET_CODEPAGE = 0x3FDE
PID_TAG_ATTACH_DATA_BINARY = 0x3701  # also PidTagAttachDataObject (PtypObject)
PID_TAG_ATTACH_FILENAME = 0x3704
PID_TAG_ATTACH_METHOD = 0x3705
PID_TAG_ATTACH_LONG_FILENAME = 0x3707
PID_TAG_ATTACH_SIZE = 0x0E20
PID_TAG_DISPLAY_NAME_A = 0x3001

ATTACH_BY_VALUE = 1
ATTACH_EMBEDDED_MESSAGE = 5


def nid_type(nid):
    return nid & 0x1F


def nid_index(nid):
    return nid >> 5


def make_nid(nid_type_, index):
    return (index << 5) | nid_type_


def filetime_to_datetime(value):
    """FILETIME (100 ns since 1601-01-01 UTC) to an aware datetime, or None for 0
    or a value past what datetime holds."""
    if not value:
        return None
    try:
        return datetime.datetime(1601, 1, 1, tzinfo=datetime.timezone.utc) + \
            datetime.timedelta(microseconds=value // 10)
    except OverflowError:
        return None


# ---------------------------------------------------------------------------
# Format variants

class _Layout:
    """Sizes and offsets that differ between ANSI, Unicode and Unicode 4 KB pages."""

    def __init__(self, kind):
        self.kind = kind
        if kind == "ansi":
            self.page_size = 512
            self.bt_meta = 496          # cEnt, cEntMax, cbEnt, cLevel (1 byte each)
            self.bt_meta_wide = False
            self.page_trailer = 500     # ptype, ptypeRepeat, wSig, bid(4), dwCRC
            self.block_trailer = 12     # cb, wSig, bid(4), dwCRC
            self.block_align = 64
            self.id_size = 4
        elif kind == "unicode":
            self.page_size = 512
            self.bt_meta = 488
            self.bt_meta_wide = False
            self.page_trailer = 496     # ptype, ptypeRepeat, wSig, dwCRC, bid(8)
            self.block_trailer = 16     # cb, wSig, dwCRC, bid(8)
            self.block_align = 64
            self.id_size = 8
        elif kind == "unicode4k":
            # PFF format document, "The 64-bit 4k page index B-tree node" and
            # "The 64-bit 4k page block"; trailer position, 512-byte allocation
            # and zlib framing measured on a real OST.
            self.page_size = 4096
            self.bt_meta = 4056         # cEnt(2), cEntMax(2), cbEnt(1), cLevel(1)
            self.bt_meta_wide = True
            self.page_trailer = 4072    # as Unicode, then 8 bytes not described
            self.block_trailer = 24     # Unicode trailer + 2 unknown, cbInflated(2), 4 unknown
            self.block_align = 512
            self.id_size = 8
        else:  # pragma: no cover
            raise ValueError(kind)

    def block_alloc(self, cb):
        a = self.block_align
        return (cb + self.block_trailer + a - 1) // a * a


class _BBTEntry:
    __slots__ = ("bid", "ib", "cb", "cref")

    def __init__(self, bid, ib, cb, cref):
        self.bid, self.ib, self.cb, self.cref = bid, ib, cb, cref


class _NBTEntry:
    __slots__ = ("nid", "bid_data", "bid_sub", "nid_parent")

    def __init__(self, nid, bid_data, bid_sub, nid_parent):
        self.nid, self.bid_data, self.bid_sub, self.nid_parent = nid, bid_data, bid_sub, nid_parent

    def __repr__(self):
        return "NBTEntry(nid=0x%x, data=0x%x, sub=0x%x, parent=0x%x)" % (
            self.nid, self.bid_data, self.bid_sub, self.nid_parent)


class _LRU:
    def __init__(self, size):
        self.size = size
        self.d = OrderedDict()

    def get(self, key):
        v = self.d.get(key)
        if v is not None:
            self.d.move_to_end(key)
        return v

    def put(self, key, value):
        self.d[key] = value
        self.d.move_to_end(key)
        if len(self.d) > self.size:
            self.d.popitem(last=False)


# ---------------------------------------------------------------------------
# NDB layer

class PSTFile:
    """An open PST or OST file. Use :func:`open_pst`."""

    def __init__(self, source):
        if hasattr(source, "read"):
            self._f = source
            self._own = False
            self.path = getattr(source, "name", None)
        else:
            self._f = open(source, "rb")
            self._own = True
            self.path = os.fspath(source)
        self._f.seek(0, os.SEEK_END)
        self.file_size = self._f.tell()
        self._page_cache = _LRU(2048)
        self._block_cache = _LRU(256)
        self._nameid = None
        self._codepage = None
        try:
            self._read_header()
        except Exception:
            self.close()
            raise

    # -- context manager
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._own and self._f:
            self._f.close()
        self._f = None

    def _read(self, offset, size):
        self._f.seek(offset)
        data = self._f.read(size)
        if len(data) != size:
            raise CorruptError("read of %d bytes at 0x%x returned %d (file is %d bytes)"
                               % (size, offset, len(data), self.file_size))
        return data

    # -- header, [MS-PST] 2.2.2.6
    def _read_header(self):
        h = self._read(0, 564) if self.file_size >= 564 else self._read(0, self.file_size)
        if h[0:4] != b"!BDN":
            raise PSTError("not a PST or OST file (no !BDN signature)")
        self.magic_client = h[8:10]
        self.ver, self.ver_client = struct.unpack_from("<HH", h, 10)
        if self.magic_client not in (b"SM", b"SO", b"AB"):
            raise PSTError("unknown wMagicClient %r" % self.magic_client)
        if self.ver in (14, 15):
            kind = "ansi"
        elif self.ver == 36:
            kind = "unicode4k"
        elif self.ver >= 23:
            kind = "unicode"
        else:
            raise UnsupportedError("wVer %d is not a known PST version" % self.ver)
        self.layout = _Layout(kind)
        self.kind = kind
        if kind == "ansi":
            self.bid_next_b, self.bid_next_p, self.unique = struct.unpack_from("<III", h, 24)
            rgnid_off = 36
            root = 164
            (self.file_eof, self.amap_last, self.amap_free, self.pmap_free,
             nbt_bid, nbt_ib, bbt_bid, bbt_ib) = struct.unpack_from("<IIIIIIII", h, root + 4)
            self.amap_valid = h[root + 36]
            self.crypt_method = h[461]
        else:
            self.bid_next_p, self.unique = struct.unpack_from("<QI", h, 32)
            rgnid_off = 44
            root = 180
            (self.file_eof, self.amap_last, self.amap_free, self.pmap_free,
             nbt_bid, nbt_ib, bbt_bid, bbt_ib) = struct.unpack_from("<QQQQQQQQ", h, root + 4)
            self.amap_valid = h[root + 68]
            self.crypt_method = h[513]
            self.bid_next_b = struct.unpack_from("<Q", h, 516)[0]
        self.rgnid = list(struct.unpack_from("<32I", h, rgnid_off))
        self.nbt_root = (nbt_bid, nbt_ib)
        self.bbt_root = (bbt_bid, bbt_ib)
        if self.crypt_method == CRYPT_EDPCRYPTED:
            raise UnsupportedError("bCryptMethod 0x10: the data is encrypted with Windows "
                                   "Information Protection and is not readable without its key")
        if self.crypt_method not in (CRYPT_NONE, CRYPT_PERMUTE, CRYPT_CYCLIC):
            raise UnsupportedError("unknown bCryptMethod 0x%02x" % self.crypt_method)

    @property
    def is_ost(self):
        return self.magic_client == b"SO"

    # -- pages and B-trees, [MS-PST] 2.2.2.7
    def _page(self, ib, ptype):
        page = self._page_cache.get(ib)
        if page is None:
            L = self.layout
            raw = self._read(ib, L.page_size)
            t = L.page_trailer
            if raw[t] != ptype or raw[t + 1] != ptype:
                raise CorruptError("page at 0x%x has type 0x%02x/0x%02x, expected 0x%02x"
                                   % (ib, raw[t], raw[t + 1], ptype))
            if L.bt_meta_wide:
                c_ent, c_ent_max, cb_ent, level = struct.unpack_from("<HHBB", raw, L.bt_meta)
            else:
                c_ent, c_ent_max, cb_ent, level = struct.unpack_from("<BBBB", raw, L.bt_meta)
            if cb_ent == 0 or c_ent * cb_ent > L.bt_meta:
                raise CorruptError("B-tree page at 0x%x: %d entries of %d bytes do not fit"
                                   % (ib, c_ent, cb_ent))
            page = (level, cb_ent, c_ent, raw)
            self._page_cache.put(ib, page)
        return page

    def _branch_entries(self, raw, c_ent, cb_ent):
        fmt = "<III" if self.layout.id_size == 4 else "<QQQ"
        return [struct.unpack_from(fmt, raw, i * cb_ent) for i in range(c_ent)]  # key, bid, ib

    def _bt_find(self, root_ib, ptype, key, leaf_key):
        """Descend from root to the leaf entry with this key; return its raw bytes or None."""
        ib = root_ib
        depth = 0
        while True:
            level, cb_ent, c_ent, raw = self._page(ib, ptype)
            if level == 0:
                lo, hi = 0, c_ent - 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    k = leaf_key(raw, mid * cb_ent)
                    if k == key:
                        return raw[mid * cb_ent:(mid + 1) * cb_ent]
                    if k < key:
                        lo = mid + 1
                    else:
                        hi = mid - 1
                return None
            entries = self._branch_entries(raw, c_ent, cb_ent)
            chosen = None
            for k, bid, child in entries:
                if k <= key:
                    chosen = child
                else:
                    break
            if chosen is None:
                return None
            ib = chosen
            depth += 1
            if depth > 32:
                raise CorruptError("B-tree deeper than 32 levels")

    def _bt_walk(self, root_ib, ptype):
        """Yield every leaf entry's raw bytes, in key order."""
        stack = [(root_ib, 0)]
        seen = set()
        while stack:
            ib, depth = stack.pop()
            if ib in seen or depth > 32:
                raise CorruptError("B-tree loop at 0x%x" % ib)
            seen.add(ib)
            level, cb_ent, c_ent, raw = self._page(ib, ptype)
            if level == 0:
                for i in range(c_ent):
                    yield raw[i * cb_ent:(i + 1) * cb_ent]
            else:
                for k, bid, child in reversed(self._branch_entries(raw, c_ent, cb_ent)):
                    stack.append((child, depth + 1))

    def _parse_bbt(self, e):
        if self.layout.id_size == 4:
            bid, ib, cb, cref = struct.unpack_from("<IIHH", e, 0)
        else:
            bid, ib, cb, cref = struct.unpack_from("<QQHH", e, 0)
        return _BBTEntry(bid, ib, cb, cref)

    def _parse_nbt(self, e):
        if self.layout.id_size == 4:
            nid, bd, bs, parent = struct.unpack_from("<IIII", e, 0)
        else:
            nid, bd, bs, parent = struct.unpack_from("<QQQI", e, 0)
            nid &= 0xFFFFFFFF
        return _NBTEntry(nid, bd, bs, parent)

    def _leaf_key_bid(self, raw, off):
        if self.layout.id_size == 4:
            return struct.unpack_from("<I", raw, off)[0] & ~1
        return struct.unpack_from("<Q", raw, off)[0] & ~1

    def _leaf_key_nid(self, raw, off):
        if self.layout.id_size == 4:
            return struct.unpack_from("<I", raw, off)[0]
        return struct.unpack_from("<Q", raw, off)[0] & 0xFFFFFFFF

    def bbt_lookup(self, bid):
        # [MS-PST] 2.2.2.2: the low bit of a BID is reserved and is ignored for lookups.
        e = self._bt_find(self.bbt_root[1], PTYPE_BBT, bid & ~1, self._leaf_key_bid)
        return self._parse_bbt(e) if e is not None else None

    def nbt_lookup(self, nid):
        e = self._bt_find(self.nbt_root[1], PTYPE_NBT, nid, self._leaf_key_nid)
        return self._parse_nbt(e) if e is not None else None

    def iter_bbt(self):
        for e in self._bt_walk(self.bbt_root[1], PTYPE_BBT):
            yield self._parse_bbt(e)

    def iter_nbt(self):
        for e in self._bt_walk(self.nbt_root[1], PTYPE_NBT):
            yield self._parse_nbt(e)

    # -- blocks, [MS-PST] 2.2.2.8
    def read_block(self, bid):
        """The data of one block, decoded and, for a 4 KB-page OST, inflated."""
        key = bid & ~1
        cached = self._block_cache.get(key)
        if cached is not None:
            return cached
        ent = self.bbt_lookup(bid)
        if ent is None:
            raise CorruptError("block 0x%x is not in the block B-tree" % bid)
        L = self.layout
        alloc = L.block_alloc(ent.cb)
        raw = self._read(ent.ib, alloc)
        tr = raw[alloc - L.block_trailer:]
        if L.kind == "ansi":
            t_cb, t_sig, t_bid, t_crc = struct.unpack_from("<HHII", tr, 0)
        else:
            t_cb, t_sig, t_crc, t_bid = struct.unpack_from("<HHIQ", tr, 0)
        if t_cb != ent.cb or (t_bid & ~1) != key:
            raise CorruptError("block 0x%x at 0x%x: trailer says bid 0x%x, %d bytes; "
                               "block B-tree says %d bytes" % (bid, ent.ib, t_bid, t_cb, ent.cb))
        data = raw[:ent.cb]
        internal = bool(bid & 2)
        if L.kind == "unicode4k":
            inflated = struct.unpack_from("<H", tr, 18)[0]
            if inflated != ent.cb:
                if self.crypt_method != CRYPT_NONE:
                    raise UnsupportedError("compressed block in an encoded 4 KB-page file; the "
                                           "order of decoding and inflating is not established")
                try:
                    data = zlib.decompress(data)
                except zlib.error as exc:
                    raise CorruptError("block 0x%x does not inflate: %s" % (bid, exc))
                if len(data) != inflated:
                    raise CorruptError("block 0x%x inflated to %d bytes, trailer says %d"
                                       % (bid, len(data), inflated))
        if not internal:
            if self.crypt_method == CRYPT_PERMUTE:
                data = crypt_permute(data)
            elif self.crypt_method == CRYPT_CYCLIC:
                data = crypt_cyclic(data, ent.bid & 0xFFFFFFFF)
        self._block_cache.put(key, data)
        return data

    def data_tree(self, bid):
        """The leaf block BIDs of a data tree, in order ([MS-PST] 2.2.2.8.3.2)."""
        if not bid:
            return []
        if not bid & 2:
            return [bid]
        out = []
        self._data_tree_into(bid, out, 0)
        return out

    def _data_tree_into(self, bid, out, depth):
        if depth > 4:
            raise CorruptError("data tree deeper than XXBLOCK")
        b = self.read_block(bid)
        btype, level, c_ent = struct.unpack_from("<BBH", b, 0)
        if btype != 0x01:
            raise CorruptError("block 0x%x: btype 0x%02x where an XBLOCK was expected" % (bid, btype))
        n = self.layout.id_size
        fmt = "<I" if n == 4 else "<Q"
        for i in range(c_ent):
            child = struct.unpack_from(fmt, b, 8 + i * n)[0]
            if level == 1:
                out.append(child)
            elif level == 2:
                self._data_tree_into(child, out, depth + 1)
            else:
                raise CorruptError("block 0x%x: XBLOCK level %d" % (bid, level))

    def subnode_tree(self, bid):
        """Map of subnode NID to (bidData, bidSub) ([MS-PST] 2.2.2.8.3.3)."""
        out = {}
        if bid:
            self._subnodes_into(bid, out, 0)
        return out

    def _subnodes_into(self, bid, out, depth):
        if depth > 4:
            raise CorruptError("subnode tree too deep")
        b = self.read_block(bid)
        btype, level, c_ent = struct.unpack_from("<BBH", b, 0)
        if btype != 0x02:
            raise CorruptError("block 0x%x: btype 0x%02x where an SLBLOCK/SIBLOCK was expected"
                               % (bid, btype))
        if self.layout.id_size == 4:
            base = 4
            if level == 0:
                for i in range(c_ent):
                    nid, bd, bs = struct.unpack_from("<III", b, base + i * 12)
                    out[nid] = (bd, bs)
            else:
                for i in range(c_ent):
                    nid, child = struct.unpack_from("<II", b, base + i * 8)
                    self._subnodes_into(child, out, depth + 1)
        else:
            base = 8
            if level == 0:
                for i in range(c_ent):
                    nid, bd, bs = struct.unpack_from("<QQQ", b, base + i * 24)
                    out[nid & 0xFFFFFFFF] = (bd, bs)
            else:
                for i in range(c_ent):
                    nid, child = struct.unpack_from("<QQ", b, base + i * 16)
                    self._subnodes_into(child, out, depth + 1)

    def node(self, nid):
        e = self.nbt_lookup(nid)
        if e is None:
            return None
        return Node(self, nid, e.bid_data, e.bid_sub, e.nid_parent)

    # -- named properties, [MS-PST] 2.4.7
    def _load_nameid(self):
        self._nameid = {}
        node = self.node(NID_NAME_TO_ID_MAP)
        if node is None:
            return
        pc = PropertyContext(node)
        guids = pc.raw(0x0002) or b""
        entries = pc.raw(0x0003) or b""
        strings = pc.raw(0x0004) or b""
        for off in range(0, len(entries) - 7, 8):
            dw, wguid, idx = struct.unpack_from("<IHH", entries, off)
            is_string = wguid & 1
            g = wguid >> 1
            if g == 1:
                guid = PS_MAPI
            elif g == 2:
                guid = PS_PUBLIC_STRINGS
            elif g >= 3 and (g - 3) * 16 + 16 <= len(guids):
                guid = uuid.UUID(bytes_le=guids[(g - 3) * 16:(g - 3) * 16 + 16])
            else:
                guid = None
            if is_string:
                if dw + 4 <= len(strings):
                    n = struct.unpack_from("<I", strings, dw)[0]
                    name = strings[dw + 4:dw + 4 + n].decode("utf-16-le", "replace")
                else:
                    name = None
                self._nameid[0x8000 + idx] = (guid, name)
            else:
                self._nameid[0x8000 + idx] = (guid, dw)

    def named_property(self, prop_id):
        """(guid, LID or name) behind a property ID from 0x8000, or None."""
        if self._nameid is None:
            self._load_nameid()
        return self._nameid.get(prop_id)

    def named_property_id(self, guid, lid_or_name):
        """The property ID this file uses for a named property, or None."""
        if self._nameid is None:
            self._load_nameid()
        guid = uuid.UUID(str(guid))
        for pid, (g, k) in self._nameid.items():
            if g == guid and k == lid_or_name:
                return pid
        return None

    # -- messaging, [MS-PST] 2.4
    def message_store(self):
        node = self.node(NID_MESSAGE_STORE)
        return PropertyContext(node) if node else None

    def root_folder(self):
        f = self.folder(NID_ROOT_FOLDER)
        if f is None:
            raise CorruptError("root folder node 0x122 is missing")
        return f

    def folder(self, nid, parent_path=""):
        node = self.node(nid)
        if node is None:
            return None
        return Folder(self, node, parent_path)

    def walk_folders(self):
        """Every folder reachable from the root through hierarchy tables, depth first."""
        seen = set()
        stack = [self.root_folder()]
        while stack:
            f = stack.pop()
            if f.nid in seen:
                continue
            seen.add(f.nid)
            yield f
            stack.extend(reversed(list(f.subfolders())))

    def message(self, nid):
        node = self.node(nid)
        return Message(self, node) if node else None

    def nid_census(self):
        """Count of NBT nodes per NID type name."""
        out = {}
        for e in self.iter_nbt():
            name = NID_TYPE_NAMES.get(nid_type(e.nid), "0x%02x" % nid_type(e.nid))
            out[name] = out.get(name, 0) + 1
        return out

    def info(self):
        return {
            "path": self.path,
            "file_size": self.file_size,
            "type": "OST" if self.is_ost else ("PAB" if self.magic_client == b"AB" else "PST"),
            "format": {"ansi": "ANSI", "unicode": "Unicode",
                       "unicode4k": "Unicode, 4 KB pages"}[self.kind],
            "wVer": self.ver,
            "wVerClient": self.ver_client,
            "crypt": CRYPT_NAMES.get(self.crypt_method, hex(self.crypt_method)),
            "header_eof": self.file_eof,
            "amap_valid": self.amap_valid,
        }


class Node:
    """An NBT node or a subnode: a data tree and an optional subnode tree."""

    def __init__(self, pst, nid, bid_data, bid_sub, nid_parent=0):
        self.pst = pst
        self.nid = nid
        self.bid_data = bid_data
        self.bid_sub = bid_sub
        self.nid_parent = nid_parent
        self._blocks = None
        self._subnodes = None

    def __repr__(self):
        return "Node(nid=0x%x, type=%s)" % (self.nid, NID_TYPE_NAMES.get(nid_type(self.nid), "?"))

    @property
    def blocks(self):
        if self._blocks is None:
            self._blocks = self.pst.data_tree(self.bid_data)
        return self._blocks

    def block(self, i):
        return self.pst.read_block(self.blocks[i])

    def iter_chunks(self):
        for b in self.blocks:
            yield self.pst.read_block(b)

    def data(self):
        return b"".join(self.iter_chunks())

    @property
    def subnodes(self):
        if self._subnodes is None:
            self._subnodes = self.pst.subnode_tree(self.bid_sub)
        return self._subnodes

    def subnode(self, nid):
        e = self.subnodes.get(nid)
        if e is None:
            return None
        return Node(self.pst, nid, e[0], e[1], self.nid)


# ---------------------------------------------------------------------------
# LTP layer, [MS-PST] 2.3

class Heap:
    """Heap-on-node ([MS-PST] 2.3.1)."""

    def __init__(self, node):
        self.node = node
        if not node.blocks:
            raise CorruptError("node 0x%x has no data for a heap" % node.nid)
        b0 = node.block(0)
        ib_hnpm, sig, self.client_sig, self.user_root = struct.unpack_from("<HBBI", b0, 0)
        if sig != HN_SIG:
            raise CorruptError("node 0x%x: heap signature 0x%02x, expected 0xEC" % (node.nid, sig))
        self._maps = {}

    def _map(self, i):
        m = self._maps.get(i)
        if m is None:
            data = self.node.block(i)
            ib = struct.unpack_from("<H", data, 0)[0]
            if ib + 4 > len(data):
                raise CorruptError("node 0x%x block %d: page map offset %d past %d bytes"
                                   % (self.node.nid, i, ib, len(data)))
            c_alloc = struct.unpack_from("<H", data, ib)[0]
            offs = struct.unpack_from("<%dH" % (c_alloc + 1), data, ib + 4)
            m = (data, offs)
            self._maps[i] = m
        return m

    def get(self, hid):
        if hid == 0:
            return b""
        if hid & 0x1F:
            raise CorruptError("0x%x is not a HID" % hid)
        index = (hid >> 5) & 0x7FF
        block = hid >> 16
        if block >= len(self.node.blocks):
            raise CorruptError("HID 0x%x: block %d of %d" % (hid, block, len(self.node.blocks)))
        data, offs = self._map(block)
        if index < 1 or index >= len(offs):
            raise CorruptError("HID 0x%x: index %d of %d allocations" % (hid, index, len(offs) - 1))
        return data[offs[index - 1]:offs[index]]

    def resolve(self, hnid):
        """An HNID: a HID in this heap, or a subnode NID of the heap's node."""
        if hnid == 0:
            return b""
        if hnid & 0x1F == 0:
            return self.get(hnid)
        sub = self.node.subnode(hnid)
        if sub is None:
            raise CorruptError("subnode 0x%x of node 0x%x is missing" % (hnid, self.node.nid))
        return sub.data()


def bth_items(heap, hid):
    """Leaf (key, data) pairs of a BTH ([MS-PST] 2.3.2)."""
    hdr = heap.get(hid)
    if len(hdr) < 8:
        raise CorruptError("BTH header too short")
    btype, cb_key, cb_ent, levels, root = struct.unpack_from("<BBBBI", hdr, 0)
    if btype != CLIENT_SIG_BTH:
        raise CorruptError("BTH header type 0x%02x" % btype)
    if root == 0:
        return
    stack = [(root, levels)]
    while stack:
        h, lvl = stack.pop()
        data = heap.get(h)
        if lvl > 0:
            rec = cb_key + 4
            children = [struct.unpack_from("<I", data, i + cb_key)[0]
                        for i in range(0, len(data) - rec + 1, rec)]
            for c in reversed(children):
                stack.append((c, lvl - 1))
        else:
            rec = cb_key + cb_ent
            for i in range(0, len(data) - rec + 1, rec):
                yield data[i:i + cb_key], data[i + cb_key:i + rec]


def decode_value(ptype, data, codepage="cp1252"):
    """A property value's bytes to a Python value ([MS-PST] 2.1.1)."""
    if data is None:
        return None
    if ptype == PT_INT16:
        return struct.unpack_from("<h", data)[0]
    if ptype == PT_INT32:
        return struct.unpack_from("<i", data)[0]
    if ptype == PT_FLOAT:
        return struct.unpack_from("<f", data)[0]
    if ptype in (PT_DOUBLE, PT_APPTIME):
        return struct.unpack_from("<d", data)[0]
    if ptype in (PT_CURRENCY, PT_INT64):
        return struct.unpack_from("<q", data)[0]
    if ptype == PT_ERROR:
        return struct.unpack_from("<I", data)[0]
    if ptype == PT_BOOLEAN:
        return bool(data[0])
    if ptype == PT_SYSTIME:
        return filetime_to_datetime(struct.unpack_from("<Q", data)[0])
    if ptype == PT_GUID:
        return uuid.UUID(bytes_le=bytes(data[:16]))
    if ptype == PT_UNICODE:
        if len(data) % 2:
            # An odd length cannot be UTF-16, so it is returned as stored, as bytes.
            # Outlook has also been seen to store 8-bit text of even length under
            # this type; that cannot be told apart and decodes as UTF-16.
            return bytes(data)
        # [MS-OXCDATA] 2.11.1: PtypString ends at its terminating U+0000. Bytes
        # stored after it are not part of the value; raw() still returns them.
        s = bytes(data).decode("utf-16-le", "replace")
        return s.split("\x00", 1)[0]
    if ptype == PT_STRING8:
        # [MS-OXCDATA] 2.11.1: PtypString8 ends at its terminating zero byte. In
        # ANSI files Outlook has been seen to leave other bytes after it.
        return _decode8(bytes(data).split(b"\x00", 1)[0], codepage)
    if ptype & PT_MV:
        base = ptype & ~PT_MV
        if base in _FIXED_SIZE:
            n = _FIXED_SIZE[base]
            return [decode_value(base, data[i:i + n], codepage) for i in range(0, len(data) - n + 1, n)]
        if base in (PT_UNICODE, PT_STRING8, PT_BINARY):
            if len(data) < 4:
                return []
            count = struct.unpack_from("<I", data, 0)[0]
            if 4 + 4 * count > len(data):
                raise CorruptError("multi-value count %d does not fit %d bytes" % (count, len(data)))
            offs = list(struct.unpack_from("<%dI" % count, data, 4)) + [len(data)]
            vals = [bytes(data[offs[i]:offs[i + 1]]) for i in range(count)]
            if base == PT_BINARY:
                return vals
            return [decode_value(base, v, codepage) for v in vals]
    return bytes(data)


def _decode8(data, codepage):
    try:
        return bytes(data).decode(codepage)
    except (LookupError, UnicodeDecodeError):
        return bytes(data).decode("cp1252", "replace")


class PropertyContext:
    """A property context ([MS-PST] 2.3.3): property ID to value."""

    def __init__(self, node, codepage=None):
        self.node = node
        self.heap = Heap(node)
        if self.heap.client_sig != CLIENT_SIG_PC:
            raise CorruptError("node 0x%x: client signature 0x%02x is not a property context"
                               % (node.nid, self.heap.client_sig))
        self.props = OrderedDict()
        for k, v in bth_items(self.heap, self.heap.user_root):
            pid = struct.unpack_from("<H", k)[0]
            ptype, hnid = struct.unpack_from("<HI", v)
            self.props[pid] = (ptype, hnid)
        self._codepage = codepage

    @property
    def codepage(self):
        if self._codepage is None:
            cp = None
            for pid in (PID_TAG_MESSAGE_CODEPAGE, PID_TAG_INTERNET_CODEPAGE):
                if pid in self.props and self.props[pid][0] == PT_INT32:
                    cp = self.props[pid][1]
                    break
            self._codepage = "cp%d" % cp if cp else "cp1252"
        return self._codepage

    def __contains__(self, pid):
        return pid in self.props

    def ptype(self, pid):
        p = self.props.get(pid)
        return p[0] if p else None

    def raw(self, pid):
        """The stored bytes of a property, or None if it is absent."""
        p = self.props.get(pid)
        if p is None:
            return None
        ptype, hnid = p
        if ptype in _PC_INLINE:
            return struct.pack("<I", hnid)[:_FIXED_SIZE[ptype]]
        return self.heap.resolve(hnid)

    def get(self, pid, default=None):
        p = self.props.get(pid)
        if p is None:
            return default
        ptype = p[0]
        if ptype == PT_OBJECT:
            return self.object_ref(pid)
        return decode_value(ptype, self.raw(pid), self.codepage)

    def object_ref(self, pid):
        """(subnode NID, size) of a PtypObject property ([MS-PST] 2.3.3.5)."""
        data = self.raw(pid)
        if data is None or len(data) < 8:
            return None
        return struct.unpack_from("<II", data, 0)

    def items(self):
        """(property ID, type, value) for every property."""
        for pid, (ptype, _) in self.props.items():
            try:
                val = self.get(pid)
            except PSTError as exc:
                val = exc
            yield pid, ptype, val


class TableContext:
    """A table context ([MS-PST] 2.3.4)."""

    def __init__(self, node):
        self.node = node
        self.heap = Heap(node)
        if self.heap.client_sig != CLIENT_SIG_TC:
            raise CorruptError("node 0x%x: client signature 0x%02x is not a table context"
                               % (node.nid, self.heap.client_sig))
        info = self.heap.get(self.heap.user_root)
        btype, c_cols = struct.unpack_from("<BB", info, 0)
        if btype != CLIENT_SIG_TC:
            raise CorruptError("TCINFO type 0x%02x" % btype)
        self.rgib = struct.unpack_from("<4H", info, 2)
        self.hid_row_index, self.hnid_rows, _ = struct.unpack_from("<III", info, 10)
        self.columns = []
        for i in range(c_cols):
            tag, ib, cb, ibit = struct.unpack_from("<IHBB", info, 22 + 8 * i)
            self.columns.append((tag, ib, cb, ibit))
        self.row_size = self.rgib[3]
        self._row_index = None
        self._rows_node = None
        self._rows_heap = None
        self._rpb = None

    @property
    def row_index(self):
        """(dwRowID, row number) pairs in row-number order."""
        if self._row_index is None:
            out = []
            if self.hid_row_index:
                for k, v in bth_items(self.heap, self.hid_row_index):
                    out.append((struct.unpack_from("<I", k)[0], int.from_bytes(v, "little")))
            out.sort(key=lambda r: r[1])
            self._row_index = out
        return self._row_index

    def __len__(self):
        return len(self.row_index)

    def _row_bytes(self, n):
        cb = self.row_size
        if self.hnid_rows == 0:
            raise CorruptError("table 0x%x has no row matrix" % self.node.nid)
        if self.hnid_rows & 0x1F == 0:
            if self._rows_heap is None:
                self._rows_heap = self.heap.get(self.hnid_rows)
            data = self._rows_heap
            return data[n * cb:(n + 1) * cb]
        if self._rows_node is None:
            self._rows_node = self.node.subnode(self.hnid_rows)
            if self._rows_node is None:
                raise CorruptError("row matrix subnode 0x%x missing" % self.hnid_rows)
            # [MS-PST] 2.3.4.4: rows do not cross blocks; every block but the last
            # is full, so the first block's length fixes the rows per block.
            self._rpb = max(1, len(self._rows_node.block(0)) // cb)
        b, r = divmod(n, self._rpb)
        if b >= len(self._rows_node.blocks):
            raise CorruptError("row %d is past the row matrix" % n)
        data = self._rows_node.block(b)
        return data[r * cb:(r + 1) * cb]

    def rows(self):
        """Each row as a dict of property ID to value, with '_row_id'."""
        for row_id, n in self.row_index:
            yield self.row(row_id, n)

    def row(self, row_id, n):
        raw = self._row_bytes(n)
        if len(raw) < self.row_size:
            raise CorruptError("row %d is cut short" % n)
        ceb = raw[self.rgib[2]:self.rgib[3]]
        out = {"_row_id": row_id}
        codepage = "cp1252"
        for tag, ib, cb, ibit in self.columns:
            if not (ceb[ibit // 8] >> (7 - ibit % 8)) & 1:
                continue
            ptype = tag & 0xFFFF
            pid = tag >> 16
            cell = raw[ib:ib + cb]
            try:
                fixed = _FIXED_SIZE.get(ptype)
                if fixed is not None and fixed <= 8 and cb == fixed:
                    val = decode_value(ptype, cell)
                else:
                    hnid = struct.unpack_from("<I", cell)[0]
                    data = self.heap.resolve(hnid)
                    if ptype == PT_OBJECT:
                        val = struct.unpack_from("<II", data) if len(data) >= 8 else None
                    else:
                        val = decode_value(ptype, data, codepage)
            except PSTError as exc:
                val = exc
            out[pid] = val
            if pid == PID_TAG_MESSAGE_CODEPAGE and isinstance(val, int) and val:
                codepage = "cp%d" % val
        return out


# ---------------------------------------------------------------------------
# Messaging layer, [MS-PST] 2.4

def _clean_subject(s):
    # [MS-PST] 2.5.3.1.1.1 / [MS-OXCMSG]: a stored subject can start with SOH and
    # the length of its prefix; the reader drops those two characters.
    if isinstance(s, str) and len(s) >= 2 and s[0] == "\x01":
        return s[2:]
    return s


class Folder:
    def __init__(self, pst, node, parent_path=""):
        self.pst = pst
        self.node = node
        self.nid = node.nid
        self.pc = PropertyContext(node)
        self.name = self.pc.get(PID_TAG_DISPLAY_NAME, "")
        if parent_path:
            self.path = parent_path.rstrip("/") + "/" + (self.name or "")
        else:
            self.path = "/" + (self.name or "")
        self.is_search_folder = nid_type(self.nid) == NID_TYPE_SEARCH_FOLDER

    def __repr__(self):
        return "Folder(0x%x, %r)" % (self.nid, self.path)

    @property
    def container_class(self):
        return self.pc.get(PID_TAG_CONTAINER_CLASS)

    @property
    def content_count(self):
        return self.pc.get(PID_TAG_CONTENT_COUNT)

    def _table(self, t):
        node = self.pst.node((self.nid & ~0x1F) | t)
        if node is None:
            return None
        return TableContext(node)

    def hierarchy_table(self):
        return self._table(NID_TYPE_HIERARCHY_TABLE)

    def contents_table(self):
        return self._table(NID_TYPE_CONTENTS_TABLE)

    def search_contents_table(self):
        """A search folder's results. Its rows name items that live in other folders."""
        return self._table(NID_TYPE_SEARCH_CONTENTS_TABLE)

    def assoc_contents_table(self):
        return self._table(NID_TYPE_ASSOC_CONTENTS_TABLE)

    def subfolder_nids(self):
        t = self.hierarchy_table()
        return [rid for rid, _ in t.row_index] if t else []

    def subfolders(self):
        for nid in self.subfolder_nids():
            f = self.pst.folder(nid, self.path)
            if f is not None:
                yield f

    def message_nids(self):
        t = self.contents_table()
        return [rid for rid, _ in t.row_index] if t else []

    def search_result_nids(self):
        t = self.search_contents_table() if self.is_search_folder else None
        return [rid for rid, _ in t.row_index] if t else []

    def assoc_message_nids(self):
        t = self.assoc_contents_table()
        return [rid for rid, _ in t.row_index] if t else []

    def messages(self):
        """Items listed in the folder's contents table. A search folder has none of its
        own (see :meth:`search_result_nids`). A row whose node is missing is skipped;
        :meth:`message_nids` lists every row."""
        for nid in self.message_nids():
            m = self.pst.message(nid)
            if m is not None:
                yield m


class Message:
    def __init__(self, pst, node):
        self.pst = pst
        self.node = node
        self.nid = node.nid
        self.pc = PropertyContext(node)

    def __repr__(self):
        return "Message(0x%x, %r)" % (self.nid, self.message_class)

    def get(self, pid, default=None):
        return self.pc.get(pid, default)

    def get_named(self, guid, lid_or_name, default=None):
        pid = self.pst.named_property_id(guid, lid_or_name)
        if pid is None:
            return default
        return self.pc.get(pid, default)

    @property
    def message_class(self):
        return self.pc.get(PID_TAG_MESSAGE_CLASS)

    @property
    def subject(self):
        return _clean_subject(self.pc.get(PID_TAG_SUBJECT))

    def recipient_table(self):
        sub = self.node.subnode(NID_RECIPIENT_TABLE)
        return TableContext(sub) if sub else None

    def recipients(self):
        t = self.recipient_table()
        return list(t.rows()) if t else []

    def attachment_table(self):
        sub = self.node.subnode(NID_ATTACHMENT_TABLE)
        return TableContext(sub) if sub else None

    def attachment_nids(self):
        t = self.attachment_table()
        return [rid for rid, _ in t.row_index] if t else []

    def attachments(self):
        out = []
        for nid in self.attachment_nids():
            sub = self.node.subnode(nid)
            if sub is None:
                raise CorruptError("attachment subnode 0x%x of message 0x%x is missing"
                                   % (nid, self.nid))
            out.append(Attachment(self, sub))
        return out


class Attachment:
    def __init__(self, message, node):
        self.message = message
        self.pst = message.pst
        self.node = node
        self.nid = node.nid
        self.pc = PropertyContext(node)

    def __repr__(self):
        return "Attachment(0x%x, %r)" % (self.nid, self.filename)

    def get(self, pid, default=None):
        return self.pc.get(pid, default)

    @property
    def method(self):
        return self.pc.get(PID_TAG_ATTACH_METHOD)

    @property
    def filename(self):
        return self.pc.get(PID_TAG_ATTACH_LONG_FILENAME) or self.pc.get(PID_TAG_ATTACH_FILENAME)

    @property
    def size(self):
        """PidTagAttachSize as stored (the attachment object's size, not the file's)."""
        return self.pc.get(PID_TAG_ATTACH_SIZE)

    @property
    def is_embedded_message(self):
        return self.pc.ptype(PID_TAG_ATTACH_DATA_BINARY) == PT_OBJECT

    def data_size(self):
        """Bytes of PidTagAttachDataBinary, or None when it is absent."""
        p = self.pc.props.get(PID_TAG_ATTACH_DATA_BINARY)
        if p is None or p[0] != PT_BINARY:
            return None
        hnid = p[1]
        if hnid == 0:
            return 0
        if hnid & 0x1F == 0:
            return len(self.pc.heap.get(hnid))
        sub = self.node.subnode(hnid)
        if sub is None:
            raise CorruptError("attachment data subnode 0x%x missing" % hnid)
        return sum(len(c) for c in sub.iter_chunks())

    def iter_data(self):
        """PidTagAttachDataBinary in pieces, so a large attachment is never held whole."""
        p = self.pc.props.get(PID_TAG_ATTACH_DATA_BINARY)
        if p is None or p[0] != PT_BINARY:
            return
        hnid = p[1]
        if hnid == 0:
            return
        if hnid & 0x1F == 0:
            yield self.pc.heap.get(hnid)
            return
        sub = self.node.subnode(hnid)
        if sub is None:
            raise CorruptError("attachment data subnode 0x%x missing" % hnid)
        yield from sub.iter_chunks()

    def data(self):
        return b"".join(self.iter_data())

    def embedded_message(self):
        """The attached item of an embedded-message attachment, or None."""
        if not self.is_embedded_message:
            return None
        ref = self.pc.object_ref(PID_TAG_ATTACH_DATA_BINARY)
        if ref is None:
            return None
        sub = self.node.subnode(ref[0])
        if sub is None:
            raise CorruptError("embedded message subnode 0x%x missing" % ref[0])
        return Message(self.pst, sub)


def open_pst(source):
    """Open a PST or OST file by path or binary file object."""
    return PSTFile(source)


# ---------------------------------------------------------------------------
# Command line

def _json_default(o):
    if isinstance(o, datetime.datetime):
        return o.isoformat()
    if isinstance(o, (bytes, bytearray)):
        return o.hex()
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, Exception):
        return "ERROR: %s" % o
    return str(o)


def _cmd_info(pst, args):
    info = pst.info()
    info["nodes_by_type"] = pst.nid_census()
    store = pst.message_store()
    if store is not None:
        info["store_display_name"] = store.get(PID_TAG_DISPLAY_NAME)
    print(json.dumps(info, indent=2, default=_json_default))


def _cmd_folders(pst, args):
    for f in pst.walk_folders():
        try:
            n = len(f.message_nids())
        except PSTError as exc:
            n = "error: %s" % exc
        print("0x%06x\t%s\t%s\t%s" % (f.nid, n, f.container_class or "", f.path))


def _message_summary(m):
    d = {"nid": m.nid, "message_class": m.message_class}
    try:
        d["recipients"] = len(m.recipients())
    except PSTError as exc:
        d["recipients"] = "error: %s" % exc
    try:
        atts = m.attachments()
        d["attachments"] = []
        for a in atts:
            ad = {"nid": a.nid, "method": a.method, "attach_size": a.size}
            if a.is_embedded_message:
                em = a.embedded_message()
                ad["embedded"] = _message_summary(em) if em else None
            else:
                ad["data_size"] = a.data_size()
            d["attachments"].append(ad)
    except PSTError as exc:
        d["attachments"] = "error: %s" % exc
    return d


def _cmd_items(pst, args):
    for f in pst.walk_folders():
        for nid in f.message_nids():
            try:
                m = pst.message(nid)
                if m is None:
                    print(json.dumps({"folder": f.path, "nid": nid, "error": "node missing"}))
                    continue
                d = {"folder": f.path}
                d.update(_message_summary(m))
                if args.subjects:
                    d["subject"] = m.subject
            except PSTError as exc:
                d = {"folder": f.path, "nid": nid, "error": str(exc)}
            print(json.dumps(d, default=_json_default, ensure_ascii=False))


def _cmd_props(pst, args):
    nid = int(args.nid, 0)
    node = pst.node(nid)
    if node is None:
        raise PSTError("no node 0x%x" % nid)
    pc = PropertyContext(node)
    for pid, ptype, val in pc.items():
        named = pst.named_property(pid) if pid >= 0x8000 else None
        label = " %s" % (named,) if named else ""
        if isinstance(val, (bytes, bytearray)) and len(val) > 64:
            val = val[:64].hex() + "... (%d bytes)" % len(val)
        print("0x%04x 0x%04x%s\t%s" % (pid, ptype, label, _json_default(val) if not isinstance(val, (int, float, str, bool, type(None))) else val))


def _cmd_extract(pst, args):
    os.makedirs(args.output, exist_ok=True)
    count = 0
    for f in pst.walk_folders():
        for m in f.messages():
            for a in m.attachments():
                if a.is_embedded_message:
                    continue
                name = "%06x_%06x_%s" % (m.nid, a.nid, os.path.basename(a.filename or "attachment"))
                target = os.path.join(args.output, name.replace(os.sep, "_"))
                with open(target, "wb") as out:
                    for chunk in a.iter_data():
                        out.write(chunk)
                count += 1
    print("%d attachments written to %s" % (count, args.output))


def main(argv=None):
    p = argparse.ArgumentParser(prog="pstprobe", description="Read Outlook PST and OST files.")
    p.add_argument("--version", action="version", version="pstprobe " + __version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, helptext in (("info", "header, format and node counts"),
                           ("folders", "folder tree with item counts"),
                           ("items", "one JSON line per item: class, recipients, attachments"),
                           ("props", "every property of one node"),
                           ("extract", "write every file attachment to a folder")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("file")
        if name == "items":
            s.add_argument("--subjects", action="store_true", help="include subjects")
        if name == "props":
            s.add_argument("nid", help="node ID, e.g. 0x21 or 2097188")
        if name == "extract":
            s.add_argument("-o", "--output", required=True)
    args = p.parse_args(argv)
    try:
        with open_pst(args.file) as pst:
            {"info": _cmd_info, "folders": _cmd_folders, "items": _cmd_items,
             "props": _cmd_props, "extract": _cmd_extract}[args.cmd](pst, args)
    except PSTError as exc:
        print("pstprobe: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
