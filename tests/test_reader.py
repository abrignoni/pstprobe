import datetime
import io
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


if __name__ == "__main__":
    unittest.main()
