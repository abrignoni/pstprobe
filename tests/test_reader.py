import contextlib
import datetime
import io
import json
import struct
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pstprobe as P  # noqa: E402
import pstbuilder  # noqa: E402

VARIANTS = [
    ("unicode", P.CRYPT_PERMUTE, False),
    ("unicode", P.CRYPT_NONE, False),
    ("unicode", P.CRYPT_CYCLIC, False),
    ("ansi", P.CRYPT_PERMUTE, False),
    ("ansi", P.CRYPT_CYCLIC, False),
    ("unicode4k", P.CRYPT_NONE, False),
    ("unicode4k", P.CRYPT_NONE, True),
]


class CryptTests(unittest.TestCase):
    def test_permute_round_trip(self):
        data = bytes(range(256)) * 3
        enc = P.crypt_permute(data, encrypt=True)
        self.assertNotEqual(enc, data)
        self.assertEqual(P.crypt_permute(enc), data)

    def test_cyclic_is_symmetric(self):
        data = bytes(range(256)) * 3
        for key in (0, 4, 0x1234, 0xFFFFFFFC, 0x00010004):
            enc = P.crypt_cyclic(data, key)
            self.assertNotEqual(enc, data)
            self.assertEqual(P.crypt_cyclic(enc, key), data)

    def test_tables_are_permutations(self):
        for t in (P._MPBB_R, P._MPBB_S, P._MPBB_I):
            self.assertEqual(sorted(t), list(range(256)))


class SampleTests(unittest.TestCase):
    def check(self, kind, crypt, compress):
        raw, facts = pstbuilder.build_sample(kind, crypt, compress)
        with P.open_pst(io.BytesIO(raw)) as pst:
            self.assertEqual(pst.kind, kind)
            self.assertEqual(pst.crypt_method, crypt)
            self.assertEqual(pst.message_store().get(P.PID_TAG_DISPLAY_NAME), "kd store")
            folders = list(pst.walk_folders())
            self.assertEqual([f.path for f in folders], ["/", "/Inbox"])
            inbox = folders[1]
            self.assertEqual(inbox.nid, facts["inbox"])
            self.assertEqual(inbox.container_class, "IPF.Note")
            self.assertEqual(inbox.content_count, 1)
            self.assertEqual(inbox.message_nids(), [facts["msg"]])
            m = list(inbox.messages())[0]
            self.assertEqual(m.message_class, "IPM.Appointment")
            self.assertEqual(m.subject, "RE: kd subject")
            self.assertEqual([r[0x3001] for r in m.recipients()], ["kd one", "kd two"])
            self.assertEqual([r[0x0C15] for r in m.recipients()], [1, 2])
            atts = m.attachments()
            self.assertEqual(len(atts), 1)
            a = atts[0]
            self.assertEqual(a.filename, "kd.bin")
            self.assertEqual(a.method, 1)
            self.assertEqual(a.size, 20100)
            self.assertFalse(a.is_embedded_message)
            self.assertEqual(a.data(), facts["att_payload"])
            self.assertEqual(a.data_size(), 20000)
            self.assertEqual(pst.named_property(0x8000), (facts["guid_appt"], 0x820D))
            when = m.get_named(facts["guid_appt"], 0x820D)
            self.assertEqual(when, datetime.datetime(2025, 11, 20, 15, 0, tzinfo=datetime.timezone.utc))
            census = pst.nid_census()
            self.assertEqual(census["NORMAL_MESSAGE"], 1)
            self.assertEqual(census["NORMAL_FOLDER"], 2)

    def test_variants(self):
        for kind, crypt, compress in VARIANTS:
            with self.subTest(kind=kind, crypt=crypt, compress=compress):
                self.check(kind, crypt, compress)

    def test_compressed_blocks_were_compressed(self):
        raw_c, _ = pstbuilder.build_sample("unicode4k", P.CRYPT_NONE, True)
        raw_u, _ = pstbuilder.build_sample("unicode4k", P.CRYPT_NONE, False)
        self.assertNotEqual(raw_c, raw_u)
        self.assertIn(b"\x78\x9c", raw_c)


class StructureTests(unittest.TestCase):
    def test_bth_index_level_and_heap_over_two_blocks(self):
        for kind in ("unicode", "ansi", "unicode4k"):
            crypt = P.CRYPT_NONE if kind == "unicode4k" else P.CRYPT_PERMUTE
            with self.subTest(kind=kind):
                raw, expected = pstbuilder.build_wide_pc_sample(kind, crypt)
                with P.open_pst(io.BytesIO(raw)) as pst:
                    node = pst.node(P.NID_MESSAGE_STORE)
                    self.assertEqual(len(node.blocks), 2)
                    pc = P.PropertyContext(node)
                    got = {pid: pc.get(pid) for pid in pc.props}
                    self.assertEqual(got, expected)


class SearchFolderTests(unittest.TestCase):
    def test_search_results_from_tc_and_ac_tables(self):
        for kind in ("unicode", "ansi", "unicode4k"):
            crypt = P.CRYPT_NONE if kind == "unicode4k" else P.CRYPT_PERMUTE
            with self.subTest(kind=kind):
                raw, facts = pstbuilder.build_sample(kind, crypt, search_tables=True)
                with P.open_pst(io.BytesIO(raw)) as pst:
                    for name in ("search_tc", "search_ac"):
                        folder = pst.folder(facts[name])
                        self.assertTrue(folder.is_search_folder)
                        self.assertEqual(folder.search_result_nids(), [facts["msg"]], name)
                        self.assertEqual(folder.message_nids(), [], name)
                    # The 0xAC table is not a table context: asking for one says so.
                    with self.assertRaises(P.UnsupportedError):
                        pst.folder(facts["search_ac"]).search_contents_table()

    def test_ac_row_ids_follow_row_numbers(self):
        # The BTH is sorted by row ID; the rows come back in row-number order.
        class OneBlockNode:
            nid = 0x730

            def __init__(self, data):
                self.blocks = [data]

            def block(self, i):
                return self.blocks[i]

        ids = [0x200064, 0x200024, 0x200044]
        node = OneBlockNode(pstbuilder.ac_table(ids))
        self.assertEqual(P.search_table_row_ids(node), ids)
        self.assertEqual(P.search_table_row_ids(OneBlockNode(pstbuilder.ac_table([]))), [])
        with self.assertRaises(P.CorruptError):
            P.search_table_row_ids(OneBlockNode(pstbuilder.Builder().tc([0x67F20003], [])))


class VerifyTests(unittest.TestCase):
    VARIANTS = (("unicode", P.CRYPT_PERMUTE, False), ("ansi", P.CRYPT_CYCLIC, False),
                ("unicode4k", P.CRYPT_NONE, True))

    def test_built_files_match(self):
        for kind, crypt, compress in self.VARIANTS:
            with self.subTest(kind=kind):
                raw, _ = pstbuilder.build_sample(kind, crypt, compress=compress)
                with P.open_pst(io.BytesIO(raw)) as pst:
                    r = pst.verify()
                self.assertTrue(r["all_match"], r)
                self.assertGreater(r["pages"]["match"], 0)
                self.assertGreater(r["blocks"]["match"], 0)
                self.assertEqual(set(r["header"]), {"partial"} if kind == "ansi" else {"partial", "full"})

    def test_one_flipped_byte_is_found_where_it_is(self):
        for kind, crypt, compress in self.VARIANTS:
            raw, _ = pstbuilder.build_sample(kind, crypt, compress=compress)
            with P.open_pst(io.BytesIO(raw)) as pst:
                ent = next(iter(pst.iter_bbt()))
                page_ib = pst.bbt_root[1]
            for where, off in (("block", ent.ib + 1), ("page", page_ib + 1), ("header", 20)):
                with self.subTest(kind=kind, where=where):
                    bad = bytearray(raw)
                    bad[off] ^= 0xFF
                    with P.open_pst(io.BytesIO(bytes(bad))) as pst:
                        r = pst.verify()
                    self.assertFalse(r["all_match"])
                    self.assertEqual(r["blocks"]["mismatch_bids"], ["0x%x" % ent.bid] if where == "block" else [])
                    self.assertEqual(r["pages"]["mismatch_offsets"], ["0x%x" % page_ib] if where == "page" else [])
                    self.assertEqual(r["header"]["partial"], where != "header")
                    self.assertEqual(r["pages"]["unreadable"] + r["blocks"]["unreadable"], 0)

    def test_cli_exit_status(self):
        raw, _ = pstbuilder.build_sample()
        with tempfile.TemporaryDirectory() as d:
            good = os.path.join(d, "good.pst")
            bad = os.path.join(d, "bad.pst")
            with open(good, "wb") as f:
                f.write(raw)
            damaged = bytearray(raw)
            damaged[20] ^= 0xFF
            with open(bad, "wb") as f:
                f.write(damaged)
            for path, status in ((good, 0), (bad, 1)):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(P.main(["verify", path]), status)
                self.assertEqual(json.loads(out.getvalue())["all_match"], status == 0)


class HardeningTests(unittest.TestCase):
    """Damaged input, code pages and output encoding."""

    class FakeNode:
        nid = 0x21

        def __init__(self, blocks, kind="ansi", codepage="cp1252"):
            self.blocks = blocks
            self.pst = type("FakePst", (), {"kind": kind, "codepage": codepage})()

        def block(self, i):
            return self.blocks[i]

    def test_4k_heap_ids_have_a_14_bit_index(self):
        h = pstbuilder.Heap(P.CLIENT_SIG_PC)
        for i in range(2100):
            h.alloc(struct.pack("<H", i))
        data = h.build()
        four_k = P.Heap(self.FakeNode([data], kind="unicode4k"))
        self.assertEqual(four_k.get(2100 << 5), struct.pack("<H", 2099))
        # the 512-byte-page layout reads index 2100 as block 0 index 52 plus a block bit
        with self.assertRaises(P.CorruptError):
            P.Heap(self.FakeNode([data], kind="unicode")).get(2100 << 5)
        mh = pstbuilder.MultiHeap(P.CLIENT_SIG_PC, "unicode4k")
        mh.alloc(b"first")
        mh.new_block()
        hid = mh.alloc(b"second")
        self.assertEqual(hid, (1 << 19) | (1 << 5))
        blocks = mh.build()
        self.assertEqual(P.Heap(self.FakeNode(blocks, kind="unicode4k")).get(hid), b"second")

    def test_8bit_strings_use_the_items_code_page(self):
        b = pstbuilder.Builder("ansi")
        cyr = "\u0418\u0432\u0430\u043d".encode("cp1251")
        name = 0x3001001E
        # PidTagMessageCodepage sorts after the name column: the row still uses it
        tc = b.tc([0x67F20003, name, 0x3FFD0003],
                  [{0x67F20003: pstbuilder.i32(1), name: cyr, 0x3FFD0003: pstbuilder.i32(1251)},
                   {0x67F20003: pstbuilder.i32(2), name: cyr}])
        rows = list(P.TableContext(self.FakeNode([tc])).rows())
        self.assertEqual(rows[0][0x3001], "\u0418\u0432\u0430\u043d")
        self.assertEqual(rows[1][0x3001], cyr.decode("cp1252"))
        rows = list(P.TableContext(self.FakeNode([tc]), "cp1251").rows())
        self.assertEqual(rows[1][0x3001], "\u0418\u0432\u0430\u043d")
        own = b.pc([(0x3001, P.PT_STRING8, cyr), (0x3FFD, P.PT_INT32, pstbuilder.i32(1251))])
        self.assertEqual(P.PropertyContext(self.FakeNode([own])).get(0x3001), "\u0418\u0432\u0430\u043d")
        plain = b.pc([(0x3001, P.PT_STRING8, cyr)])
        self.assertEqual(P.PropertyContext(self.FakeNode([plain])).get(0x3001), cyr.decode("cp1252"))
        self.assertEqual(P.PropertyContext(self.FakeNode([plain]), "cp1251").get(0x3001), "\u0418\u0432\u0430\u043d")
        self.assertEqual(P.PropertyContext(self.FakeNode([plain], codepage="cp1251")).get(0x3001),
                         "\u0418\u0432\u0430\u043d")
        # PidTagInternetCodepage names the body's code page, not the properties'
        inet = b.pc([(0x3001, P.PT_STRING8, cyr), (0x3FDE, P.PT_INT32, pstbuilder.i32(1251))])
        self.assertEqual(P.PropertyContext(self.FakeNode([inet])).get(0x3001), cyr.decode("cp1252"))
        # numbers outside the cpNNNN names map through the codec table
        ascii_ = b.pc([(0x3001, P.PT_STRING8, b"kd"), (0x3FFD, P.PT_INT32, pstbuilder.i32(20127))])
        self.assertEqual(P.PropertyContext(self.FakeNode([ascii_])).codepage, "ascii")

    def test_8bit_body_falls_back_when_it_is_not_in_the_internet_code_page(self):
        b = pstbuilder.Builder("ansi")
        text = "\u53d7\u4fe1"
        stored = text.encode("cp932")
        for inet, want in ((65001, text), (20127, text), (932, text)):
            with self.subTest(inet=inet):
                pc = b.pc([(P.PID_TAG_BODY, P.PT_STRING8, stored),
                           (0x3FFD, P.PT_INT32, pstbuilder.i32(932)),
                           (0x3FDE, P.PT_INT32, pstbuilder.i32(inet))])
                self.assertEqual(P.Message(None, self.FakeNode([pc])).body(), want)
        # a body that decodes in the Internet code page is read in it
        pc = b.pc([(P.PID_TAG_BODY, P.PT_STRING8, "\u0418".encode("cp1251")),
                   (0x3FDE, P.PT_INT32, pstbuilder.i32(1251))])
        self.assertEqual(P.Message(None, self.FakeNode([pc])).body(), "\u0418")

    def test_codepage_option(self):
        raw, _ = pstbuilder.build_sample("ansi")
        with P.open_pst(io.BytesIO(raw), codepage=1251) as pst:
            self.assertEqual(pst.codepage, "cp1251")
        with self.assertRaises(P.UnsupportedError):
            P.open_pst(io.BytesIO(raw), codepage=99999)

    def test_a_short_value_does_not_lose_the_others(self):
        pc = pstbuilder.Builder().pc([(0x0E06, P.PT_SYSTIME, b""), (0x3001, P.PT_UNICODE, pstbuilder.u16("kd"))])
        got = {pid: val for pid, _, val in P.PropertyContext(self.FakeNode([pc], kind="unicode")).items()}
        self.assertIsInstance(got[0x0E06], P.CorruptError)
        self.assertEqual(got[0x3001], "kd")

    def test_export_of_a_folder_that_lists_itself(self):
        raw, facts = pstbuilder.build_sample(self_loop=True)
        with tempfile.TemporaryDirectory() as d, P.open_pst(io.BytesIO(raw)) as pst:
            counts = P.export_folder(pst, pst.folder(facts["inbox"]), os.path.join(d, "out"), recursive=True)
        self.assertEqual((counts["folders"], counts["folders_repeated"], counts["items"]), (1, 1, 1))

    def test_an_xxblock_under_an_xxblock_is_refused(self):
        # Nested XXBLOCKs pass a depth cap of 4 and multiply the work by their fan-out at
        # each level; [MS-PST] 2.2.2.8.3.2 allows only XBLOCKs below an XXBLOCK.
        b = pstbuilder.Builder("unicode")
        leaf = b.data(b"kd")
        x = 0x1000 | 2
        inner = 0x1004 | 2
        outer = 0x1008 | 2
        b.blocks[x] = struct.pack("<BBHIQ", 1, 1, 1, 2, leaf)
        b.blocks[inner] = struct.pack("<BBHIQ", 1, 2, 1, 2, x)
        b.blocks[outer] = struct.pack("<BBHIQ", 1, 2, 1, 2, inner)
        b.node(P.NID_MESSAGE_STORE, b.pc([]))
        with P.open_pst(io.BytesIO(b.build())) as pst:
            self.assertEqual(pst.data_tree(inner), [leaf])
            with self.assertRaises(P.CorruptError):
                pst.data_tree(outer)

    def test_long_names_are_cut_by_bytes(self):
        name = "\u6587" * 120 + ".pdf"
        cut = P.safe_filename(name)
        self.assertLessEqual(len(cut.encode("utf-8")), 180)
        self.assertTrue(cut.endswith(".pdf"))
        self.assertEqual(P.safe_filename("short.txt"), "short.txt")

    def test_cli_writes_utf8_and_reports_os_errors(self):
        import subprocess
        raw, _ = pstbuilder.build_sample(inbox_name="\u53d7\u4fe1\u30c8\u30ec\u30a4")
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pstprobe.py")
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cjk.pst")
            with open(path, "wb") as f:
                f.write(raw)
            for argv, status in ((["folders", path], 0), (["info", os.path.join(d, "missing.pst")], 1),
                                 (["info", d], 1)):
                done = subprocess.run([sys.executable, script] + argv, capture_output=True, env=env,
                                      stdin=subprocess.DEVNULL, timeout=60, check=False)
                self.assertEqual(done.returncode, status, done.stderr)
                self.assertNotIn(b"Traceback", done.stderr)
            done = subprocess.run([sys.executable, script, "folders", path], capture_output=True, env=env,
                                  stdin=subprocess.DEVNULL, timeout=60, check=False)
            self.assertIn("\u53d7\u4fe1\u30c8\u30ec\u30a4".encode("utf-8"), done.stdout)


class RefusalTests(unittest.TestCase):
    def test_not_a_pst(self):
        with self.assertRaises(P.PSTError):
            P.open_pst(io.BytesIO(b"\0" * 1024))

    def test_wip_encrypted_is_refused(self):
        raw, _ = pstbuilder.build_sample("unicode", P.CRYPT_NONE)
        raw = bytearray(raw)
        raw[513] = P.CRYPT_EDPCRYPTED
        with self.assertRaises(P.UnsupportedError):
            P.open_pst(io.BytesIO(bytes(raw)))

    def test_truncated_file_raises(self):
        raw, _ = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE)
        cut = raw[:len(raw) // 3]
        with P.open_pst(io.BytesIO(cut)) as pst:
            with self.assertRaises(P.CorruptError):
                list(pst.walk_folders())

    def test_compressed_and_encoded_is_refused(self):
        raw, _ = pstbuilder.build_sample("unicode4k", P.CRYPT_NONE, True)
        raw = bytearray(raw)
        raw[513] = P.CRYPT_PERMUTE
        with P.open_pst(io.BytesIO(bytes(raw))) as pst:
            with self.assertRaises(P.UnsupportedError):
                pst.message_store()

    def test_odd_length_unicode_returned_as_bytes(self):
        self.assertEqual(P.decode_value(P.PT_UNICODE, b"IPF.Note\x00"), b"IPF.Note\x00")
        self.assertEqual(P.decode_value(P.PT_UNICODE, "ab\x00".encode("utf-16-le")), "ab")

    def test_strings_end_at_terminator(self):
        self.assertEqual(P.decode_value(P.PT_STRING8, b"kd\x00\xee\xd0junk"), "kd")
        self.assertEqual(P.decode_value(P.PT_UNICODE, "kd\x00junk!".encode("utf-16-le")), "kd")
        self.assertEqual(P.decode_value(P.PT_STRING8, b"kd"), "kd")


class CliTests(unittest.TestCase):
    def test_password_reported_not_enforced(self):
        raw, facts = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE, password=0xE61E9B0F)
        with P.open_pst(io.BytesIO(raw)) as pst:
            info = pst.info()
            self.assertIs(info["password_set"], True)
            self.assertEqual(info["password_crc32"], "0xe61e9b0f")
            self.assertEqual([f.path for f in pst.walk_folders()], ["/", "/Inbox"])
        for value, expect in ((0, False), (None, False)):
            raw, _ = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE, password=value)
            with P.open_pst(io.BytesIO(raw)) as pst:
                self.assertIs(pst.info()["password_set"], expect)
        raw, _ = pstbuilder.build_sample("unicode4k", P.CRYPT_NONE, True, password=0xFFFFFFFF)
        with P.open_pst(io.BytesIO(raw)) as pst:
            info = pst.info()
            self.assertEqual(info["type"], "OST")
            self.assertIsNone(info["password_set"])
            self.assertEqual(info["password_crc32"], "0xffffffff")

    def test_info_and_items(self):
        import contextlib
        import json
        import tempfile
        raw, facts = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.pst")
            with open(path, "wb") as f:
                f.write(raw)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(P.main(["info", path]), 0)
            info = json.loads(buf.getvalue())
            self.assertEqual(info["format"], "Unicode")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(P.main(["items", path]), 0)
            item = json.loads(buf.getvalue().splitlines()[0])
            self.assertEqual(item["nid"], facts["msg"])
            self.assertEqual(item["recipients"], 2)
            self.assertEqual(item["attachments"][0]["data_size"], 20000)
            out = os.path.join(d, "att")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(P.main(["extract", path, "-o", out]), 0)
            files = os.listdir(out)
            self.assertEqual(len(files), 1)
            with open(os.path.join(out, files[0]), "rb") as f:
                self.assertEqual(f.read(), facts["att_payload"])


# [MS-OXRTFCP] v20250520 3.1.1 and 3.1.2: the two worked examples.
RTF_EXAMPLE_1 = bytes.fromhex("2d0000002b0000004c5a4675f1c5c7a703000a007263706731323542320af320"
                              "68656c090020627705b06c647d0a800fa0")
RTF_EXAMPLE_2 = bytes.fromhex("1a0000001c0000004c5a4675e2d44b51410004205758595a0d6e7d010eb0")


class RtfTests(unittest.TestCase):
    def test_spec_examples(self):
        self.assertEqual(P.decompress_rtf(RTF_EXAMPLE_1),
                         b"{\\rtf1\\ansi\\ansicpg1252\\pard hello world}\r\n")
        # The specification's text prints "{\\ref1" here. The first token is a reference to
        # dictionary offset 0 for 6 bytes, which holds "{\\rtf1", and the CRC matches.
        self.assertEqual(P.decompress_rtf(RTF_EXAMPLE_2), b"{\\rtf1 WXYZWXYZWXYZWXYZWXYZ}")
        self.assertEqual(P.rtf_header(RTF_EXAMPLE_1),
                         {"compsize": 0x2d, "rawsize": 0x2b, "comptype": "LZFu", "crc": 0xa7c7c5f1})

    def test_crc_matches_spec_table(self):
        # 2.1.2.2.1 lists the table; rebuild it from the reflected polynomial and run 2.1.3.2.
        table = []
        for n in range(256):
            c = n
            for _ in range(8):
                c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
            table.append(c)
        self.assertEqual(table[1], 0x77073096)
        crc = 0
        for byte in RTF_EXAMPLE_1[16:]:
            crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
        self.assertEqual(crc, 0xa7c7c5f1)
        self.assertEqual(P.rtf_crc(RTF_EXAMPLE_1[16:]), crc)

    def test_uncompressed_is_copied_to_the_end(self):
        raw = struct.pack("<IIII", 12 + 5, 3, 0x414C454D, 0) + b"{\\rtf}"
        self.assertEqual(P.decompress_rtf(raw), b"{\\rtf}")

    def test_corrupt_inputs(self):
        bad_crc = bytearray(RTF_EXAMPLE_1)
        bad_crc[12] ^= 1
        bad_type = bytearray(RTF_EXAMPLE_1)
        bad_type[8:12] = b"ABCD"
        # a byte of PADDING inside COMPSIZE counts in the CRC (2.2.3), so the CRC no longer matches
        padded = bytearray(RTF_EXAMPLE_1) + b"\0"
        struct.pack_into("<I", padded, 0, len(padded) - 4)
        too_big = bytearray(RTF_EXAMPLE_1)
        struct.pack_into("<I", too_big, 0, len(too_big))  # COMPSIZE past the end of the value
        for raw in (bytes(bad_crc), bytes(bad_type), RTF_EXAMPLE_1[:-3], RTF_EXAMPLE_1[:10],
                    bytes(padded), bytes(too_big)):
            with self.assertRaises(P.CorruptError):
                P.decompress_rtf(raw)
        # bytes after 4 + COMPSIZE are outside the stream and change nothing
        self.assertEqual(P.decompress_rtf(RTF_EXAMPLE_1 + b"\0\0"), P.decompress_rtf(RTF_EXAMPLE_1))

    def test_message_rtf_body_and_export(self):
        import json
        import tempfile
        raw, facts = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE, rtf=RTF_EXAMPLE_1)
        with tempfile.TemporaryDirectory() as d:
            with P.open_pst(io.BytesIO(raw)) as pst:
                m = pst.message(facts["msg"])
                self.assertEqual(m.rtf_body(), P.decompress_rtf(RTF_EXAMPLE_1))
                self.assertIsNone(m.body())
                counts = P.export_folder(pst, pst.root_folder(), d, recursive=True)
            self.assertEqual(counts["rtf_bodies"], 1)
            inbox = os.path.join(d, "Inbox_%06x" % facts["inbox"])
            with open(os.path.join(inbox, "items.jsonl"), encoding="utf-8") as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["rtf_file"], "bodies/%06x.rtf" % facts["msg"])
            with open(os.path.join(inbox, rec["rtf_file"]), "rb") as f:
                self.assertEqual(f.read(), P.decompress_rtf(RTF_EXAMPLE_1))


# [MS-OXRTFEX] v20250520 3.1, as printed (one line's indent, a page break in the PDF, removed).
RTFEX_EXAMPLE_3_1 = (
    b'{\\rtf1\\ANSI\\ansicpg1251\\fromhtml1 \\deff0\n'
    b'{\\fonttbl {\\f0\\fmodern Courier New;}{\\f1\\fswiss Arial;}{\\f2\\fswiss\\fcharset0 Arial;}}\n'
    b'{\\colortbl\\red0\\green0\\blue0;\\red0\\green0\\blue255;}\n'
    b'{\\*\\htmltag64}\n'
    b'\\uc1\\pard\\plain\\deftab360 \\f0\\fs24\n'
    b'{\\*\\htmltag <HTML><head>\\par\n'
    b'<style>\\par\n'
    b'<!--\\par\n'
    b' /* Style Definitions */\\par\n'
    b' p.MsoNormal, li.MsoNormal \\{font-family:Arial;\\}\\par\n'
    b'-->\\par\n'
    b'</style>\\par\n'
    b'\\tab <!-- This is a HTML comment.\\par\n'
    b'There is a horizontal tab (%x09) character before the comment, \\par\n'
    b'and some new lines inside the comment. -->\\par\n'
    b'</head>\\par\n'
    b'<body>\\par\n'
    b'<p\\par\n'
    b'class="MsoNormal">}\n'
    b'{\\htmlrtf \\f1 \\htmlrtf0 Note the line break inside a P tag. {\\*\\htmltag <b>}{\\htmlrtf \\b\n'
    b'\\htmlrtf0 This is a bold text{\\*\\htmltag </b>}} \\htmlrtf\\par\\htmlrtf0}\n'
    b'\\htmlrtf \\par \\htmlrtf0\n'
    b'{\\*\\htmltag </p>\\par\n'
    b'<p class="MsoNormal">\\par}\n'
    b'{\\htmlrtf \\f1 \\htmlrtf0 This is a normal text with a character references:\n'
    b"{\\*\\htmltag &nbsp;}\\htmlrtf \\'a0\\htmlrtf0 {\\*\\htmltag &lt;}\\htmlrtf <\\htmlrtf0 {\\*\\htmltag\n"
    b"&uml;}\\htmlrtf {\\f2\\'a8}\\htmlrtf0{\\*\\htmltag <br>\\par}\\htmlrtf\\line\\htmlrtf0\n"
    b'characters which have special meaning in RTF: \\{\\}\\\\{\\*\\htmltag\n'
    b'<br>\\par}\\htmlrtf\\line\\htmlrtf0\\htmlrtf\\par\\htmlrtf0}\n'
    b'{\\*\\htmltag </p>\\par\n'
    b'<ol>\\par\n'
    b'<li class="MsoNormal">}{\\htmlrtf\n'
    b'{{\\*\\pn\\pnlvlbody\\pndec\\pnstart1\\pnindent360{\\pntxta.}}\\li360\\fi-360{\\pntext 1.\\tab} \\f1\n'
    b'\\htmlrtf0 This is a list item}\\htmlrtf\\par\\htmlrtf0}\n'
    b'{\\*\\htmltag \\par\n'
    b'</ol>\\par\n'
    b'</body>\\par\n'
    b'</HTML>\\par }}\n'
)


class RtfDeencapsulationTests(unittest.TestCase):
    def test_spec_example(self):
        kind, html = P.deencapsulate_rtf(RTFEX_EXAMPLE_3_1)
        self.assertEqual(kind, "html")
        # The specification's source HTML for this example, with the three places where its
        # own RTF says otherwise: "a bold", "which", and no space before each entity (each
        # space in the RTF there ends a control word).
        want = ('<HTML><head>\r\n<style>\r\n<!--\r\n /* Style Definitions */\r\n'
                ' p.MsoNormal, li.MsoNormal {font-family:Arial;}\r\n-->\r\n</style>\r\n'
                '\t<!-- This is a HTML comment.\r\nThere is a horizontal tab (%x09) character '
                'before the comment, \r\nand some new lines inside the comment. -->\r\n'
                '</head>\r\n<body>\r\n<p\r\nclass="MsoNormal">Note the line break inside a P '
                'tag. <b>This is a bold text</b> </p>\r\n<p class="MsoNormal">\r\nThis is a '
                'normal text with a character references:&nbsp;&lt;&uml;<br>\r\ncharacters '
                'which have special meaning in RTF: {}\\<br>\r\n</p>\r\n<ol>\r\n'
                '<li class="MsoNormal">This is a list item\r\n</ol>\r\n</body>\r\n</HTML>\r\n')
        self.assertEqual(html, want)

    def test_recognition(self):
        self.assertEqual(P.rtf_encapsulation(b"{\\rtf1\\ansi\\fromtext x}"), "text")
        self.assertEqual(P.rtf_encapsulation(b"{\\rtf1\\ansi\\fromhtml1 x}"), "html")
        self.assertIsNone(P.rtf_encapsulation(b"{\\rtf1\\ansi\\fromhtml x}"))   # must be \fromhtml1
        self.assertIsNone(P.rtf_encapsulation(b"{\\rtf1 x\\fromtext}"))          # text comes first
        self.assertIsNone(P.rtf_encapsulation(b"{\\rtf1" + b"\\a" * 9 + b"\\fromtext}"))  # 11th
        self.assertIsNone(P.rtf_encapsulation(b"{\\rtf2\\fromtext}"))
        self.assertEqual(P.deencapsulate_rtf(b"{\\rtf1\\ansi plain rtf}"), (None, None))

    def text(self, body, head=b"\\ansi\\ansicpg1252"):
        kind, text = P.deencapsulate_rtf(b"{\\rtf1" + head + b"\\fromtext" + body + b"}")
        self.assertEqual(kind, "text")
        return text

    def test_text_rules(self):
        self.assertEqual(self.text(b" a\\par b\\line c\\tab d"), "a\r\nb\r\nc\td")
        self.assertEqual(self.text(b" caf\\'e9 \\{x\\} \\\\"), "café {x} \\")
        # CR and LF are not text; the CR ends \fromtext, so the space after it is text
        self.assertEqual(self.text(b"\r\n ab\r\ncd"), " abcd")
        self.assertEqual(self.text(b"{\\colortbl\\red0;}x"), "x")         # a destination is skipped
        self.assertEqual(self.text(b"{\\*\\unknownword hidden}x"), "x")   # so is any \* group
        self.assertEqual(self.text(b" \\lquote q\\rquote  \\emdash"), "‘q’ —")

    def test_unicode_and_fallback(self):
        self.assertEqual(self.text(b" \\u8364?x"), "€x")              # \uc1 by default
        self.assertEqual(self.text(b"\\uc2 \\u8364??x"), "€x")
        self.assertEqual(self.text(b"\\uc0 \\u8364 x"), "€x")
        self.assertEqual(self.text(b" \\u-4064?"), "\uf020")               # negative values
        self.assertEqual(self.text(b" \\u8364\\'80x"), "€x")          # \'hh counts as one
        self.assertEqual(self.text(b"{\\uc2 \\u8364??}\\u8364?x"), "€€x")  # scoped

    def test_surrogates_and_range(self):
        # [MSFT-RTF]: a character above U+FFFF is two \uN, its UTF-16 surrogate pair
        self.assertEqual(self.text(b" \\u-10179?\\u-8704?x"), "\U0001f600x")
        self.assertEqual(P.rtf_original_bytes("text", self.text(b" \\u-10179?\\u-8704?")),
                         "\U0001f600".encode("utf-8"))
        self.assertEqual(P.rtf_original_bytes("html", self.text(b" \\u-10179?\\u-8704?")),
                         b"&#128512;")
        for lone in (b" \\u-10179?x", b" \\u-8704?x", b" \\u99999?x"):
            self.assertEqual(self.text(lone), "�x")      # unpaired, or not 16-bit
        self.assertEqual(self.text(b" \\u-10179?"), "�")  # unpaired at the end
        self.assertEqual(self.text(b" \\u-10179?\\u65?"), "�A")

    def test_backslash_newline_is_par(self):
        # [MSFT-RTF] "Special Characters": CR or LF after a backslash is a \par
        self.assertEqual(self.text(b" a\\\r\nb"), "a\r\nb")
        self.assertEqual(self.text(b" a\\\nb"), "a\r\nb")

    def test_code_pages(self):
        fonts = b"{\\fonttbl{\\f0\\fcharset0 A;}{\\f1\\fcharset204 B;}{\\f2\\fcharset0\\cpg1253 C;}}"
        self.assertEqual(self.text(fonts + b"\\f1 \\'c0\\f0 \\'c0\\f2 \\'c1"), "АÀΑ")
        self.assertEqual(self.text(b" \\'c0", head=b"\\ansi\\ansicpg1251"), "А")
        # a double-byte code page: the two bytes of one character arrive as two \'hh
        jp = b"{\\fonttbl{\\f0\\fcharset128 M;}}\\f0 \\'82\\'a0"
        self.assertEqual(self.text(jp), "あ")

    def test_fields_upr_and_binary(self):
        field = b"{\\field{\\*\\fldinst HYPERLINK \"http://x\"}{\\fldrslt link}}"
        self.assertEqual(self.text(b" see " + field), "see link")
        self.assertEqual(self.text(b"{\\upr{\\b ansi}{\\*\\ud{\\b uni\\u8364?}}}"), "uni€")
        self.assertEqual(self.text(b"{\\pict\\bin3 }}}x}y"), "y")

    def test_htmlrtf_scoping(self):
        rtf = (b"{\\rtf1\\ansi\\fromhtml1 {\\*\\htmltag <p>}\\htmlrtf {a\\htmlrtf0 b} c\\htmlrtf0 d"
               b"{\\*\\htmltag </p>}}")
        self.assertEqual(P.deencapsulate_rtf(rtf), ("html", "<p>bd</p>"))


def mela(rtf):
    """An uncompressed PidTagRtfCompressed value ([MS-OXRTFCP] 2.1.3.1.1, COMPTYPE MELA)."""
    return struct.pack("<IIII", len(rtf) + 12, len(rtf), 0x414C454D, 0) + rtf


class RtfAttachmentPlacementTests(unittest.TestCase):
    def placement(self, rtf, att_props=()):
        raw, facts = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE, rtf=mela(rtf),
                                             att_props=att_props)
        with P.open_pst(io.BytesIO(raw)) as pst:
            text, placed = pst.message(facts["msg"]).rtf_attachment_placement()
            return text, [(a.filename, offset, how) for a, offset, how in placed]

    def test_objattph_places_the_attachment(self):
        text, placed = self.placement(b"{\\rtf1\\ansi see:\\objattph\\'20 end}")
        self.assertEqual(text, "see:  end")
        self.assertEqual(placed, [("kd.bin", 4, "objattph")])

    def test_count_mismatch_puts_attachments_at_the_end(self):
        # [MS-OXRTFEX] 2.2.3.4: two \\objattph, one attachment: the positions are ignored
        text, placed = self.placement(b"{\\rtf1\\ansi a\\objattph b\\objattph c}")
        self.assertEqual(placed, [("kd.bin", len(text), "end")])
        text, placed = self.placement(b"{\\rtf1\\ansi no position}")
        self.assertEqual(placed, [("kd.bin", len(text), "end")])

    def test_objattph_in_a_skipped_destination_is_not_counted(self):
        text, placed = self.placement(b"{\\rtf1\\ansi {\\*\\x \\objattph}a\\objattph b}")
        self.assertEqual((text, placed), ("ab", [("kd.bin", 1, "objattph")]))  # the space ends the word

    def test_hidden_and_unrendered_attachments_are_left_out(self):
        rtf = b"{\\rtf1\\ansi a\\objattph b}"
        hidden = [(P.PID_TAG_ATTACHMENT_HIDDEN, P.PT_BOOLEAN, struct.pack("<I", 1))]
        self.assertEqual(self.placement(rtf, hidden)[1], [])
        unrendered = [(P.PID_TAG_RENDERING_POSITION, P.PT_INT32, struct.pack("<I", 0xFFFFFFFF))]
        self.assertEqual(self.placement(rtf, unrendered)[1], [])
        shown = [(P.PID_TAG_RENDERING_POSITION, P.PT_INT32, struct.pack("<I", 0))]
        self.assertEqual(self.placement(rtf, shown)[1], [("kd.bin", 1, "objattph")])

    def test_html_rtf_has_no_placement(self):
        self.assertEqual(self.placement(b"{\\rtf1\\ansi\\fromhtml1 {\\*\\htmltag <p>}}"), (None, []))


class ExportTests(unittest.TestCase):
    def test_export_folder_writes_items_and_attachments(self):
        import json
        import tempfile
        raw, facts = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE)
        with tempfile.TemporaryDirectory() as d:
            with P.open_pst(io.BytesIO(raw)) as pst:
                counts = P.export_folder(pst, pst.root_folder(), d, recursive=True)
            self.assertEqual(counts, {"folders": 2, "items": 1, "attachments": 1, "rtf_bodies": 0,
                                      "errors": 0, "folders_repeated": 0})
            inbox = os.path.join(d, "Inbox_%06x" % facts["inbox"])
            with open(os.path.join(inbox, "items.jsonl"), encoding="utf-8") as f:
                rec = json.loads(f.readline())
            self.assertEqual(rec["nid"], facts["msg"])
            self.assertEqual(rec["subject"], "RE: kd subject")
            self.assertEqual(rec["properties"]["0x0037"]["name"], "PidTagSubject")
            self.assertEqual(rec["properties"]["0x8000"]["name"], "PidLidAppointmentStartWhole")
            self.assertEqual(rec["properties"]["0x8000"]["value"], "2025-11-20T15:00:00+00:00")
            self.assertEqual(len(rec["recipients"]), 2)
            with open(os.path.join(inbox, rec["attachments"][0]["file"]), "rb") as f:
                self.assertEqual(f.read(), facts["att_payload"])
            with open(os.path.join(inbox, "folder.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["path"], "/Inbox")

    def test_second_export_to_the_same_folder_is_refused_and_changes_nothing(self):
        import tempfile
        raw, _ = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE, rtf=RTF_EXAMPLE_1)

        def snapshot(top):
            out = {}
            for base, _dirs, files in os.walk(top):
                for name in files:
                    with open(os.path.join(base, name), "rb") as f:
                        out[os.path.relpath(os.path.join(base, name), top)] = f.read()
            return out
        with tempfile.TemporaryDirectory() as d, P.open_pst(io.BytesIO(raw)) as pst:
            P.export_folder(pst, pst.root_folder(), d, recursive=True)
            before = snapshot(d)
            for recursive in (True, False):
                with self.assertRaises(FileExistsError):
                    P.export_folder(pst, pst.root_folder(), d, recursive=recursive)
                self.assertEqual(snapshot(d), before)
            # the CLI says so and exits 1
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(P.main(["export", "--recursive", "-o", d, self._write(d, raw)]), 1)
            self.assertIn("will not replace", err.getvalue())

    @staticmethod
    def _write(d, raw):
        path = os.path.join(d, "in.pst")  # inside the temporary folder, removed with it
        with open(path, "wb") as f:
            f.write(raw)
        return path

    def test_attachments_of_attached_items_do_not_collide(self):
        import json
        import tempfile
        raw, payloads = pstbuilder.build_nested_sample("unicode", P.CRYPT_PERMUTE)
        with tempfile.TemporaryDirectory() as d:
            with P.open_pst(io.BytesIO(raw)) as pst:
                counts = P.export_folder(pst, pst.root_folder(), d, recursive=True)
                names = [n for f in pst.walk_folders() for m in f.messages()
                         for n, _a in P.iter_file_attachments(m)]
            self.assertEqual(counts["attachments"], 2)
            self.assertEqual(len(set(names)), 2)
            inbox = [x for x in os.listdir(d) if x.startswith("Inbox_")][0]
            with open(os.path.join(d, inbox, "items.jsonl"), encoding="utf-8") as f:
                recs = [json.loads(line) for line in f]
            for rec in recs:
                inner = rec["attachments"][0]["embedded_item"]["attachments"][0]
                with open(os.path.join(d, inbox, inner["file"]), "rb") as f:
                    self.assertEqual(f.read(), payloads[rec["nid"]])
            out = os.path.join(d, "x")
            with contextlib.redirect_stdout(io.StringIO()):
                path = os.path.join(d, "n.pst")
                with open(path, "wb") as f:
                    f.write(raw)
                self.assertEqual(P.main(["extract", path, "-o", out]), 0)
            self.assertEqual(len(os.listdir(out)), 2)

    def test_write_attachment_refuses_to_replace(self):
        import tempfile
        raw, _ = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE)
        with tempfile.TemporaryDirectory() as d, P.open_pst(io.BytesIO(raw)) as pst:
            a = list(list(pst.walk_folders())[1].messages())[0].attachments()[0]
            path = os.path.join(d, "a.bin")
            P.write_attachment(a, path)
            with self.assertRaises(FileExistsError):
                P.write_attachment(a, path)

    def test_safe_filename(self):
        self.assertEqual(P.safe_filename('a/b\\c:d*?.txt'), "a_b_c_d__.txt")
        self.assertEqual(P.safe_filename("CON.txt"), "_CON.txt")
        self.assertEqual(P.safe_filename(" .. "), "attachment")
        self.assertEqual(P.safe_filename(None), "attachment")


class WindowTest(unittest.TestCase):
    """The window is built in a child process: one Tk root per process."""

    def test_the_window_builds_and_shows_a_file(self):
        import json
        import subprocess
        import tempfile
        try:
            import tkinter
        except ImportError as error:
            if os.environ.get("PSTPROBE_REQUIRE_GUI"):
                raise
            self.skipTest("no Tk: %s" % error)
        if sys.platform == "darwin" and tkinter.TkVersion >= 9 and not os.environ.get("PSTPROBE_REQUIRE_GUI"):
            # Tk 9.0.3 (python.org 3.14) aborts on macOS and pops a crash dialog on the desktop
            self.skipTest("Tk 9 on macOS; run this test with a Python that ships Tk 8.6")
        raw, _ = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE, rtf=RTF_EXAMPLE_1)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.pst")
            with open(path, "wb") as f:
                f.write(raw)
            done = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "..", "pstprobe_gui.py"),
                                   "--selfcheck", path], capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=120, check=False)
        if done.returncode != 0:
            no_display = done.returncode > 0 and "TclError" in done.stderr and "display" in done.stderr
            if no_display and not os.environ.get("PSTPROBE_REQUIRE_GUI"):
                self.skipTest("no display")
            self.fail("window self-check failed (%d): %s" % (done.returncode, done.stderr[-500:]))
        self.assertEqual(json.loads(done.stdout), {
            "window": "built", "menus": 4 + (sys.platform == "darwin"), "folders_shown": 2,
            "items_shown": 1, "properties_shown": 4, "recipients_shown": 2, "attachments_shown": 1,
            # the sample's RTF is a document, not encapsulated: the window shows its text
            # ("hello world") first, with the one attachment marked at the end
            "bodies_shown": ["from-rtf", "rtf"], "body_chars_shown": 57, "body_bytes_saved": 11,
            "attachment_files_saved": 1, "exported_items": 1})


if __name__ == "__main__":
    unittest.main()
