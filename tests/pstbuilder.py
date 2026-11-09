"""Build small synthetic PST files for the tests.

Written from [MS-PST] for the ANSI and Unicode formats and from the layout pstprobe
reads for 4 KB-page OSTs. Nothing here comes from a real mailbox. A file built here
proves the reader agrees with this writer; agreement with Outlook is shown by the
corpus checks in tools/, not by these tests.
"""
import struct
import zlib

import pstprobe as P


class Heap:
    """One-block heap-on-node: allocations addressed by HID (index << 5)."""

    def __init__(self, client_sig):
        self.client_sig = client_sig
        self.allocs = []
        self.user_root = 0

    def alloc(self, data):
        self.allocs.append(bytes(data))
        return len(self.allocs) << 5

    def build(self):
        body = bytearray(struct.pack("<HBBI4s", 0, P.HN_SIG, self.client_sig, self.user_root, b"\0" * 4))
        offs = [len(body)]
        for a in self.allocs:
            body += a
            offs.append(len(body))
        if len(body) % 2:
            body += b"\0"
        ib = len(body)
        body += struct.pack("<HH", len(self.allocs), 0)
        body += struct.pack("<%dH" % len(offs), *offs)
        struct.pack_into("<H", body, 0, ib)
        return bytes(body)


def bth(heap, cb_key, cb_ent, records):
    """records: list of (key_bytes, data_bytes); one leaf level."""
    records = sorted(records, key=lambda r: int.from_bytes(r[0], "little"))
    leaf = b"".join(k + d for k, d in records)
    root = heap.alloc(leaf) if records else 0
    return heap.alloc(struct.pack("<BBBBI", P.CLIENT_SIG_BTH, cb_key, cb_ent, 0, root))


class Builder:
    def __init__(self, kind="unicode", crypt=P.CRYPT_PERMUTE):
        self.kind = kind
        self.L = P._Layout(kind)
        self.crypt = crypt
        self.blocks = {}       # bid -> plain data
        self.nodes = {}        # nid -> (bid_data, bid_sub, parent)
        self.next_bid = 4
        self.inflate = set()   # 4 KB layout: bids to store compressed

    # -- blocks
    def _bid(self, internal=False):
        b = self.next_bid
        self.next_bid += 4
        return b | (2 if internal else 0)

    def data(self, payload, compress=False):
        """Store payload as one block, or a data tree when it is larger than a block."""
        maxb = 8176 if self.kind != "ansi" else 8180
        if len(payload) <= maxb:
            bid = self._bid()
            self.blocks[bid] = bytes(payload)
            if compress:
                self.inflate.add(bid)
            return bid
        leaves = []
        for i in range(0, len(payload), maxb):
            leaves.append(self.data(payload[i:i + maxb]))
        n = self.L.id_size
        fmt = "<I" if n == 4 else "<Q"
        x = struct.pack("<BBHI", 1, 1, len(leaves), len(payload)) + b"".join(struct.pack(fmt, b) for b in leaves)
        bid = self._bid(internal=True)
        self.blocks[bid] = x
        return bid

    def subnodes(self, entries):
        """entries: list of (nid, bid_data, bid_sub) -> SLBLOCK bid."""
        entries = sorted(entries)
        if self.L.id_size == 4:
            body = struct.pack("<BBH", 2, 0, len(entries)) + b"".join(struct.pack("<III", *e) for e in entries)
        else:
            body = struct.pack("<BBHI", 2, 0, len(entries), 0) + b"".join(struct.pack("<QQQ", *e) for e in entries)
        bid = self._bid(internal=True)
        self.blocks[bid] = body
        return bid

    def node(self, nid, payload, sub=0, parent=0):
        self.nodes[nid] = (self.data(payload), sub, parent)

    # -- LTP helpers
    def pc(self, props):
        """props: list of (pid, ptype, value bytes). Returns heap bytes."""
        h = Heap(P.CLIENT_SIG_PC)
        recs = []
        for pid, ptype, val in props:
            if ptype in P._PC_INLINE:
                hnid = int.from_bytes(val.ljust(4, b"\0"), "little")
            elif isinstance(val, int):          # already an HNID (subnode)
                hnid = val
            else:
                hnid = h.alloc(val) if val else 0
            recs.append((struct.pack("<H", pid), struct.pack("<HI", ptype, hnid)))
        h.user_root = bth(h, 2, 6, recs)
        return h.build()

    def tc(self, columns, rows):
        """columns: list of (tag); rows: list of dict tag -> bytes (fixed) or bytes for variable.
        Row ID is the value of tag 0x67F20003. Rows go in the heap."""
        h = Heap(P.CLIENT_SIG_TC)
        cols = []
        ib = 0
        layout = []
        # 4-byte-or-larger fixed first, then 2, then 1, per [MS-PST] 2.3.4.1.
        def width(tag):
            t = tag & 0xFFFF
            f = P._FIXED_SIZE.get(t)
            return f if f is not None and f <= 8 else 4
        ordered = [t for t in columns if width(t) >= 4] + [t for t in columns if width(t) == 2] + \
                  [t for t in columns if width(t) == 1]
        for t in ordered:
            layout.append((t, ib, width(t)))
            ib += width(t)
        rgib = [sum(w for _, _, w in layout if w >= 4), 0, 0, 0]
        rgib[1] = rgib[0] + sum(w for _, _, w in layout if w == 2)
        rgib[2] = rgib[1] + sum(w for _, _, w in layout if w == 1)
        ceb_len = (len(layout) + 7) // 8
        rgib[3] = rgib[2] + ceb_len
        row_bytes = b""
        index = []
        for n, row in enumerate(rows):
            r = bytearray(rgib[3])
            for i, (t, off, w) in enumerate(layout):
                if t not in row:
                    continue
                v = row[t]
                ptype = t & 0xFFFF
                f = P._FIXED_SIZE.get(ptype)
                if f is not None and f <= 8:
                    r[off:off + w] = v[:w].ljust(w, b"\0")
                else:
                    hnid = h.alloc(v) if v else 0
                    r[off:off + 4] = struct.pack("<I", hnid)
                r[rgib[2] + i // 8] |= 1 << (7 - i % 8)
            row_bytes += bytes(r)
            rid = struct.unpack("<I", row[0x67F20003][:4])[0]
            index.append((struct.pack("<I", rid), struct.pack("<I" if self.kind != "ansi" else "<H", n)))
        hid_rows = h.alloc(row_bytes) if rows else 0
        hid_index = bth(h, 4, 4 if self.kind != "ansi" else 2, index)
        info = struct.pack("<BB4HIII", P.CLIENT_SIG_TC, len(layout), *rgib, hid_index, hid_rows, 0)
        info += b"".join(struct.pack("<IHBB", t, off, w, i) for i, (t, off, w) in enumerate(layout))
        h.user_root = h.alloc(info)
        return h.build()

    # -- file
    def build(self):
        L = self.L
        out = bytearray(L.page_size * 2 if L.page_size == 4096 else 0x4400)
        placed = {}
        for bid in sorted(self.blocks):
            data = self.blocks[bid]
            if not bid & 2:
                if self.crypt == P.CRYPT_PERMUTE:
                    data = P.crypt_permute(data, encrypt=True)
                elif self.crypt == P.CRYPT_CYCLIC:
                    data = P.crypt_cyclic(data, bid)
            inflated = len(data)
            if bid in self.inflate:
                data = zlib.compress(data)
            cb = len(data)
            alloc = L.block_alloc(cb)
            ib = len(out)
            blk = bytearray(alloc)
            blk[:cb] = data
            if L.kind == "ansi":
                tr = struct.pack("<HHII", cb, 0, bid, 0)
            elif L.kind == "unicode":
                tr = struct.pack("<HHIQ", cb, 0, 0, bid)
            else:
                tr = struct.pack("<HHIQHHI", cb, 0, 0, bid, 2, inflated, 0)
            blk[alloc - len(tr):] = tr
            out += blk
            placed[bid] = (ib, cb)
        # page alignment
        while len(out) % L.page_size:
            out.append(0)
        n = L.id_size
        if n == 4:
            bbt = [struct.pack("<IIHH", b, ib, cb, 1) for b, (ib, cb) in sorted(placed.items())]
            nbt = [struct.pack("<IIII", nid, d, s, p) for nid, (d, s, p) in sorted(self.nodes.items())]
        else:
            bbt = [struct.pack("<QQHHI", b, ib, cb, 1, 0) for b, (ib, cb) in sorted(placed.items())]
            nbt = [struct.pack("<QQQII", nid, d, s, p, 0) for nid, (d, s, p) in sorted(self.nodes.items())]
        bbt_ib = self._tree(out, bbt, P.PTYPE_BBT, 0x1000)
        nbt_ib = self._tree(out, nbt, P.PTYPE_NBT, 0x2000)
        self._header(out, nbt_ib, bbt_ib)
        return bytes(out)

    def _tree(self, out, entries, ptype, page_bid):
        """Leaf pages, plus one branch page over them when one leaf is not enough.
        Returns the root page's offset."""
        L = self.L
        per = L.bt_meta // len(entries[0])
        if len(entries) <= per:
            ib = len(out)
            out += self._leaf_page(entries, ptype, page_bid | 2)
            return ib
        n = L.id_size
        key_fmt = "<I" if n == 4 else "<Q"
        branch = []
        for i in range(0, len(entries), per):
            chunk = entries[i:i + per]
            ib = len(out)
            bid = (page_bid + 4 * (len(branch) + 1)) | 2
            out += self._leaf_page(chunk, ptype, bid)
            key = struct.unpack_from(key_fmt, chunk[0])[0]
            branch.append(struct.pack("<III" if n == 4 else "<QQQ", key, bid, ib))
        ib = len(out)
        out += self._leaf_page(branch, ptype, page_bid | 2, level=1)
        return ib

    def _leaf_page(self, entries, ptype, bid, level=0):
        L = self.L
        cb_ent = len(entries[0])
        if len(entries) * cb_ent > L.bt_meta:
            raise ValueError("too many entries for one leaf page")
        page = bytearray(L.page_size)
        page[:len(entries) * cb_ent] = b"".join(entries)
        if L.bt_meta_wide:
            struct.pack_into("<HHBB", page, L.bt_meta, len(entries), L.bt_meta // cb_ent, cb_ent, level)
        else:
            struct.pack_into("<BBBB", page, L.bt_meta, len(entries), L.bt_meta // cb_ent, cb_ent, level)
        t = L.page_trailer
        if L.kind == "ansi":
            struct.pack_into("<BBHII", page, t, ptype, ptype, 0, bid, 0)
        else:
            struct.pack_into("<BBHIQ", page, t, ptype, ptype, 0, 0, bid)
        return page

    def _header(self, out, nbt_ib, bbt_ib):
        h = bytearray(564)
        h[0:4] = b"!BDN"
        if self.kind == "ansi":
            h[8:10] = b"SM"
            struct.pack_into("<HH", h, 10, 14, 19)
            struct.pack_into("<IIIIIIII", h, 168, len(out), 0, 0, 0, 0x1004 | 2, nbt_ib, 0x1000 | 2, bbt_ib)
            h[460] = 0x80
            h[461] = self.crypt
        else:
            h[8:10] = b"SO" if self.kind == "unicode4k" else b"SM"
            struct.pack_into("<HH", h, 10, 36 if self.kind == "unicode4k" else 23, 19)
            struct.pack_into("<QQQQQQQQ", h, 184, len(out), 0, 0, 0, 0x1004 | 2, nbt_ib, 0x1000 | 2, bbt_ib)
            h[512] = 0x80
            h[513] = self.crypt
        out[0:564] = h


# 2025-11-20 15:00:00 UTC as a FILETIME: (seconds from 1601-01-01 to 1970-01-01
# plus the Unix time) times 10,000,000.
WHEN_FILETIME = (11644473600 + 1763650800) * 10_000_000


def u16(s):
    return s.encode("utf-16-le")


def i32(v):
    return struct.pack("<i", v)


def build_sample(kind="unicode", crypt=P.CRYPT_PERMUTE, compress=False, password=None, rtf=None):
    """A store with a root folder, one child folder 'Inbox' holding one message that has
    two recipients, one file attachment of 20,000 bytes (a data tree) and one named
    property. Returns (bytes, facts)."""
    b = Builder(kind, crypt)
    att_payload = bytes((i * 7) & 0xFF for i in range(20000))
    guid_appt = P.uuid.UUID("00062002-0000-0000-c000-000000000046")

    # name-to-ID map: one entry, LID 0x820D in PSETID_Appointment -> 0x8000
    nameid = b.pc([
        (0x0001, P.PT_INT32, i32(251)),
        (0x0002, P.PT_BINARY, guid_appt.bytes_le),
        (0x0003, P.PT_BINARY, struct.pack("<IHH", 0x820D, (3 << 1) | 0, 0)),
        (0x0004, P.PT_BINARY, b""),
    ])
    b.node(P.NID_NAME_TO_ID_MAP, nameid)
    store = [(P.PID_TAG_DISPLAY_NAME, P.PT_UNICODE, u16("kd store"))]
    if password is not None:
        store.append((P.PID_TAG_PST_PASSWORD, P.PT_INT32, struct.pack("<I", password)))
    b.node(P.NID_MESSAGE_STORE, b.pc(store))

    inbox = P.make_nid(P.NID_TYPE_NORMAL_FOLDER, 0x401)
    root = P.NID_ROOT_FOLDER
    msg = P.make_nid(P.NID_TYPE_NORMAL_MESSAGE, 0x10001)

    b.node(root, b.pc([(P.PID_TAG_DISPLAY_NAME, P.PT_UNICODE, u16(""))]), parent=root)
    b.node((root & ~0x1F) | P.NID_TYPE_HIERARCHY_TABLE,
           b.tc([0x67F20003, 0x30010000 | P.PT_UNICODE],
                [{0x67F20003: i32(inbox), 0x30010000 | P.PT_UNICODE: u16("Inbox")}]))
    b.node((root & ~0x1F) | P.NID_TYPE_CONTENTS_TABLE, b.tc([0x67F20003], []))
    b.node(inbox, b.pc([(P.PID_TAG_DISPLAY_NAME, P.PT_UNICODE, u16("Inbox")),
                        (P.PID_TAG_CONTAINER_CLASS, P.PT_UNICODE, u16("IPF.Note")),
                        (P.PID_TAG_CONTENT_COUNT, P.PT_INT32, i32(1))]), parent=root)
    b.node((inbox & ~0x1F) | P.NID_TYPE_HIERARCHY_TABLE, b.tc([0x67F20003], []))
    b.node((inbox & ~0x1F) | P.NID_TYPE_CONTENTS_TABLE,
           b.tc([0x67F20003, 0x0037001F], [{0x67F20003: i32(msg), 0x0037001F: u16("\x01\x04RE: kd subject")}]))

    # message subnodes: recipients, attachment table, attachment object, attachment data
    att_nid = P.make_nid(P.NID_TYPE_ATTACHMENT, 1)
    att_data_nid = P.make_nid(P.NID_TYPE_LTP, 0x10)
    att_data_bid = b.data(att_payload)
    att_pc = b.pc([(P.PID_TAG_ATTACH_METHOD, P.PT_INT32, i32(1)),
                   (P.PID_TAG_ATTACH_LONG_FILENAME, P.PT_UNICODE, u16("kd.bin")),
                   (P.PID_TAG_ATTACH_SIZE, P.PT_INT32, i32(20100)),
                   (P.PID_TAG_ATTACH_DATA_BINARY, P.PT_BINARY, att_data_nid)])
    att_sub = b.subnodes([(att_data_nid, att_data_bid, 0)])
    recip = b.tc([0x67F20003, 0x3001001F, 0x0C150003],
                 [{0x67F20003: i32(0), 0x3001001F: u16("kd one"), 0x0C150003: i32(1)},
                  {0x67F20003: i32(1), 0x3001001F: u16("kd two"), 0x0C150003: i32(2)}])
    atable = b.tc([0x67F20003, 0x37050003], [{0x67F20003: i32(att_nid), 0x37050003: i32(1)}])
    sub = b.subnodes([
        (P.NID_RECIPIENT_TABLE, b.data(recip), 0),
        (P.NID_ATTACHMENT_TABLE, b.data(atable), 0),
        (att_nid, b.data(att_pc), att_sub),
    ])
    when = struct.pack("<Q", WHEN_FILETIME)
    props = [(P.PID_TAG_MESSAGE_CLASS, P.PT_UNICODE, u16("IPM.Appointment")),
             (P.PID_TAG_SUBJECT, P.PT_UNICODE, u16("\x01\x04RE: kd subject")),
             (0x8000, P.PT_SYSTIME, when)]
    if rtf is not None:
        props.append((P.PID_TAG_RTF_COMPRESSED, P.PT_BINARY, rtf))
    b.node(msg, b.pc(props), sub=sub, parent=inbox)
    if compress:
        b.inflate.update(bid for bid in b.blocks if not bid & 2)
    facts = {"inbox": inbox, "msg": msg, "att_payload": att_payload, "guid_appt": guid_appt}
    return b.build(), facts


class MultiHeap:
    """A heap over several blocks. Block 0 starts with HNHDR, later blocks with
    HNPAGEHDR ([MS-PST] 2.3.1.2, 2.3.1.3). Kept under 8 blocks, so no HNBITMAPHDR."""

    def __init__(self, client_sig):
        self.client_sig = client_sig
        self.blocks = [[]]
        self.user_root = 0

    def new_block(self):
        self.blocks.append([])

    def alloc(self, data):
        blk = len(self.blocks) - 1
        self.blocks[blk].append(bytes(data))
        return (blk << 16) | (len(self.blocks[blk]) << 5)

    def build(self):
        out = []
        for i, allocs in enumerate(self.blocks):
            if i == 0:
                body = bytearray(struct.pack("<HBBI4s", 0, P.HN_SIG, self.client_sig, self.user_root, b"\0" * 4))
            else:
                body = bytearray(struct.pack("<H", 0))
            offs = [len(body)]
            for a in allocs:
                body += a
                offs.append(len(body))
            if len(body) % 2:
                body += b"\0"
            ib = len(body)
            body += struct.pack("<HH", len(allocs), 0) + struct.pack("<%dH" % len(offs), *offs)
            struct.pack_into("<H", body, 0, ib)
            out.append(bytes(body))
        return out


def build_wide_pc_sample(kind="unicode", crypt=P.CRYPT_PERMUTE, nprops=300):
    """A message store PC with nprops string properties, its BTH one index level deep,
    and the heap split over two blocks held by an XBLOCK. Returns (bytes, {pid: text})."""
    b = Builder(kind, crypt)
    h = MultiHeap(P.CLIENT_SIG_PC)
    expected = {}
    recs = []
    for i in range(nprops):
        pid = 0x6000 + i
        text = "kd value %04d" % i
        expected[pid] = text
        if i == nprops // 2:
            h.new_block()
        recs.append((pid, h.alloc(u16(text))))
    # leaf chunks of 40 records, then one index level over them
    leaves = []
    for i in range(0, len(recs), 40):
        chunk = recs[i:i + 40]
        data = b"".join(struct.pack("<HHI", pid, P.PT_UNICODE, hid) for pid, hid in chunk)
        leaves.append((chunk[0][0], h.alloc(data)))
    index = b"".join(struct.pack("<HI", key, hid) for key, hid in leaves)
    root = h.alloc(index)
    h.user_root = h.alloc(struct.pack("<BBBBI", P.CLIENT_SIG_BTH, 2, 6, 1, root))
    blocks = h.build()
    leaf_bids = [b.data(x) for x in blocks]
    n = b.L.id_size
    fmt = "<I" if n == 4 else "<Q"
    x = struct.pack("<BBHI", 1, 1, len(leaf_bids), sum(len(x) for x in blocks)) + \
        b"".join(struct.pack(fmt, bid) for bid in leaf_bids)
    xbid = b._bid(internal=True)
    b.blocks[xbid] = x
    b.nodes[P.NID_MESSAGE_STORE] = (xbid, 0, 0)
    return b.build(), expected


def build_nested_sample(kind="unicode", crypt=P.CRYPT_PERMUTE):
    """Two items in Inbox. Each has one attachment (local NID 0x25) that is an attached item,
    and that item has one file attachment (also local NID 0x25) named same.txt. The two
    same.txt files hold different bytes. Returns (bytes, {item NID: payload})."""
    b = Builder(kind, crypt)
    b.node(P.NID_MESSAGE_STORE, b.pc([(P.PID_TAG_DISPLAY_NAME, P.PT_UNICODE, u16("kd store"))]))
    b.node(P.NID_NAME_TO_ID_MAP, b.pc([(0x0001, P.PT_INT32, i32(251))]))
    root = P.NID_ROOT_FOLDER
    inbox = P.make_nid(P.NID_TYPE_NORMAL_FOLDER, 0x401)
    items = [P.make_nid(P.NID_TYPE_NORMAL_MESSAGE, 0x10001), P.make_nid(P.NID_TYPE_NORMAL_MESSAGE, 0x10002)]
    b.node(root, b.pc([(P.PID_TAG_DISPLAY_NAME, P.PT_UNICODE, u16(""))]), parent=root)
    b.node((root & ~0x1F) | P.NID_TYPE_HIERARCHY_TABLE,
           b.tc([0x67F20003], [{0x67F20003: i32(inbox)}]))
    b.node(inbox, b.pc([(P.PID_TAG_DISPLAY_NAME, P.PT_UNICODE, u16("Inbox"))]), parent=root)
    b.node((inbox & ~0x1F) | P.NID_TYPE_CONTENTS_TABLE,
           b.tc([0x67F20003], [{0x67F20003: i32(n)} for n in items]))
    att = P.make_nid(P.NID_TYPE_ATTACHMENT, 1)        # 0x25, local to each item
    embedded = P.make_nid(P.NID_TYPE_NORMAL_MESSAGE, 0x20)
    data_nid = P.make_nid(P.NID_TYPE_LTP, 0x10)
    payloads = {}
    for n, item in enumerate(items):
        payload = ("kd payload of item %d\n" % n).encode() * (n + 1)
        payloads[item] = payload
        inner_att_pc = b.pc([(P.PID_TAG_ATTACH_METHOD, P.PT_INT32, i32(1)),
                             (P.PID_TAG_ATTACH_LONG_FILENAME, P.PT_UNICODE, u16("same.txt")),
                             (P.PID_TAG_ATTACH_DATA_BINARY, P.PT_BINARY, payload)])
        inner_table = b.tc([0x67F20003], [{0x67F20003: i32(att)}])
        inner_sub = b.subnodes([(P.NID_ATTACHMENT_TABLE, b.data(inner_table), 0),
                                (att, b.data(inner_att_pc), 0)])
        inner_msg = b.pc([(P.PID_TAG_MESSAGE_CLASS, P.PT_UNICODE, u16("IPM.Note")),
                          (P.PID_TAG_SUBJECT, P.PT_UNICODE, u16("inner %d" % n))])
        outer_att_pc = b.pc([(P.PID_TAG_ATTACH_METHOD, P.PT_INT32, i32(5)),
                             (P.PID_TAG_ATTACH_DATA_BINARY, P.PT_OBJECT, struct.pack("<II", embedded, 0))])
        outer_att_sub = b.subnodes([(embedded, b.data(inner_msg), inner_sub)])
        outer_table = b.tc([0x67F20003], [{0x67F20003: i32(att)}])
        sub = b.subnodes([(P.NID_ATTACHMENT_TABLE, b.data(outer_table), 0),
                          (att, b.data(outer_att_pc), outer_att_sub)])
        b.node(item, b.pc([(P.PID_TAG_MESSAGE_CLASS, P.PT_UNICODE, u16("IPM.Note")),
                           (P.PID_TAG_SUBJECT, P.PT_UNICODE, u16("outer %d" % n))]), sub=sub, parent=inbox)
    del data_nid
    return b.build(), payloads
