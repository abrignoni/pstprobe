import contextlib
import datetime
import io
import struct
import os
import sys
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


class ExportTests(unittest.TestCase):
    def test_export_folder_writes_items_and_attachments(self):
        import json
        import tempfile
        raw, facts = pstbuilder.build_sample("unicode", P.CRYPT_PERMUTE)
        with tempfile.TemporaryDirectory() as d:
            with P.open_pst(io.BytesIO(raw)) as pst:
                counts = P.export_folder(pst, pst.root_folder(), d, recursive=True)
            self.assertEqual(counts, {"folders": 2, "items": 1, "attachments": 1, "rtf_bodies": 0,
                                      "errors": 0})
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
            "bodies_shown": ["rtf"], "body_chars_shown": 43, "body_bytes_saved": 43,
            "attachment_files_saved": 1, "exported_items": 1})


if __name__ == "__main__":
    unittest.main()
