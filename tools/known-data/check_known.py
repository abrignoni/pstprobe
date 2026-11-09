"""Check pstprobe against the known-data PSTs made on 2026-11-09.

    python3 tools/known-data/check_known.py /path/to/windows11_arm_outlook_pst_known_20261109.zip
    python3 tools/known-data/check_known.py --stress /path/to/windows11_arm_outlook_pst_stress_known_20261109.zip

The zip is the corpus key windows11_arm_outlook_pst_known_20261109 (Outlook LTSC 2024,
a /PIM profile with no account). The expected values below are the counts Outlook
itself reported when the items were made, the SHA-256 of the file that was attached,
and appointment times set in the VM's time zone (UTC-5). Exits non-zero on any
difference.
"""
import datetime
import hashlib
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import pstprobe  # noqa: E402

UTC = datetime.timezone.utc
ATTACH_SHA256 = "628d39a71155b542b30f19077e333450b8c184ceaeeaeedc967287446e3251ff"
APPT = "00062002-0000-0000-c000-000000000046"   # PSETID_Appointment

EXPECTED = {
    "kd_outlook/pst/Outlook Data File - kd_pim.pst": {
        "kind": "unicode",
        "classes": {
            "Inbox": {"IPM.Note": 1}, "kd_folder": {"IPM.Note": 1}, "Drafts": {"IPM.Note": 4},
            "Contacts": {"IPM.Contact": 2}, "Calendar": {"IPM.Appointment": 3},
            "Tasks": {"IPM.Task": 2}, "Notes": {"IPM.StickyNote": 1}, "Journal": {"IPM.Activity": 2},
            "Deleted Items": {"IPM.Contact": 1, "IPM.Note": 1, "IPM.StickyNote": 1},
        },
        "recipients": 6, "file_attachments": 1, "embedded": 1,
        # PidLidAppointmentStartWhole of the three appointments, sorted
        "starts": [datetime.datetime(2026, 11, 20, 15, 0, tzinfo=UTC),
                   datetime.datetime(2026, 11, 23, 14, 0, tzinfo=UTC),
                   datetime.datetime(2026, 11, 25, 5, 0, tzinfo=UTC)],
    },
    "kd_outlook/pst/kd_ansi.pst": {
        "kind": "ansi",
        "classes": {"kd_ansi_mail": {"IPM.Note": 2}, "kd_ansi_contacts": {"IPM.Contact": 1}},
        "recipients": 3, "file_attachments": 1, "embedded": 0, "starts": [],
    },
}


def check(name, data, exp):
    bad = []
    with pstprobe.open_pst(io.BytesIO(data)) as pst:
        if pst.kind != exp["kind"]:
            bad.append("format %s" % pst.kind)
        classes, recips, files, embedded, starts = {}, 0, 0, 0, []
        for f in pst.walk_folders():
            for m in f.messages():
                c = classes.setdefault(f.name, {})
                c[m.message_class] = c.get(m.message_class, 0) + 1
                recips += len(m.recipients())
                for a in m.attachments():
                    if a.is_embedded_message:
                        embedded += 1
                        if a.embedded_message() is None:
                            bad.append("embedded item 0x%x unreadable" % a.nid)
                    else:
                        files += 1
                        if hashlib.sha256(a.data()).hexdigest() != ATTACH_SHA256:
                            bad.append("attachment 0x%x hash" % a.nid)
                if m.message_class == "IPM.Appointment":
                    starts.append(m.get_named(APPT, 0x820D))
        if classes != exp["classes"]:
            bad.append("classes %r" % classes)
        for k, v in (("recipients", recips), ("file_attachments", files), ("embedded", embedded)):
            if v != exp[k]:
                bad.append("%s %d, expected %d" % (k, v, exp[k]))
        if sorted(starts) != exp["starts"]:
            bad.append("appointment starts %r" % sorted(starts))
    print("%s: %s" % (name, "ok" if not bad else "; ".join(bad)))
    return not bad


BIG_SHA256 = "6638db666458b55b703d7d312a7965f7fef18dc518a28154050cd283c9e0d39d"


def check_stress(data):
    """The stress key: 1,200 kd_bulk items, the 10,000,000-byte attachment, 350 attachments."""
    bad = []
    with pstprobe.open_pst(io.BytesIO(data)) as pst:
        drafts = [f for f in pst.walk_folders() if f.name == "Drafts"]
        msgs = list(drafts[0].messages()) if drafts else []
        if len(msgs) != 1207:
            bad.append("Drafts holds %d items" % len(msgs))
        if sum(1 for m in msgs if (m.subject or "").startswith("kd_bulk_")) != 1200:
            bad.append("kd_bulk items")
        sizes = sorted(len(m.attachments()) for m in msgs)
        if sizes[-1] != 350:
            bad.append("largest attachment count %d" % sizes[-1])
        for m in msgs:
            for a in m.attachments():
                if a.data_size() == 10000000:
                    h = hashlib.sha256()
                    for chunk in a.iter_data():
                        h.update(chunk)
                    if h.hexdigest() != BIG_SHA256:
                        bad.append("10 MB attachment hash")
                    break
            else:
                continue
            break
        else:
            bad.append("10 MB attachment not found")
    print("stress: %s" % ("ok" if not bad else "; ".join(bad)))
    return not bad


def main():
    if sys.argv[1] == "--stress":
        with zipfile.ZipFile(sys.argv[2]) as z:
            return 0 if check_stress(z.read("kd_outlook/pst/Outlook Data File - kd_pim.pst")) else 1
    ok = True
    with zipfile.ZipFile(sys.argv[1]) as z:
        for name, exp in EXPECTED.items():
            ok &= check(name, z.read(name), exp)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
