"""Read every item of a PST or OST with pstprobe and check it against the file's own records.

    python3 tools/read_all.py file.pst [more files]

Needs no other reader. For each file it walks every folder and every item in it, reads
the recipients and attachments, reads each attached file's data to the end, and opens
attached items (and their attachments, to depth 8). Checks, each against a second record
in the file itself:

- each folder's item count against its PidTagContentCount;
- the bytes read from each attached file against its data size;
- an attached file's data size against its PidTagAttachSize (data plus overhead, so it
  must not be smaller);
- each compressed RTF body (PidTagRtfCompressed) against its own CRC, which
  `decompress_rtf` checks, and its decompressed length against the header's RAWSIZE;
- each item's recipient count against the names in its PidTagDisplayTo, Cc and Bcc. A
  display name holding ";" miscounts, so these are tallied, not counted as failures.

Prints one JSON line per file. Values are counts only; no subjects, names or contents.
Exit status 1 if any file had a failure.
"""
import json
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pstprobe  # noqa: E402

DISPLAY_TAGS = (0x0E04, 0x0E03, 0x0E02)  # PidTagDisplayTo, PidTagDisplayCc, PidTagDisplayBcc


def check_message(m, r, depth):
    rtf_value = m.pc.get(0x1009)  # PidTagRtfCompressed
    if rtf_value is not None:
        r["rtf_bodies"] += 1
        rtf = m.rtf_body()  # raises CorruptError on a bad CRC
        header = pstprobe.rtf_header(rtf_value)
        if header["comptype"] == "LZFu" and header["rawsize"] != len(rtf):
            r["failures"]["RTF length differs from RAWSIZE"] += 1
    recips = m.recipients()
    r["recipients"] += len(recips)
    if depth == 0:
        listed = 0
        for tag in DISPLAY_TAGS:
            v = m.pc.get(tag)
            if isinstance(v, str):
                listed += len([x for x in v.split(";") if x.strip()])
        r["display_list"]["agree" if listed == len(recips) else "differ"] += 1
    for a in m.attachments():
        r["attachments"] += 1
        if a.is_embedded_message:
            r["attached_items"] += 1
            if depth < 8:
                inner = a.embedded_message()
                if inner is None:
                    r["failures"]["attached item not opened"] += 1
                else:
                    check_message(inner, r, depth + 1)
            continue
        size = a.data_size()
        if size is None:
            r["attachments_without_data"] += 1
            continue
        got = 0
        for chunk in a.iter_data():
            got += len(chunk)
        r["attachment_bytes"] += got
        if got != size:
            r["failures"]["attachment bytes read differ from data size"] += 1
        stored = a.pc.get(0x0E20)  # PidTagAttachSize
        if isinstance(stored, int) and size > stored:
            r["failures"]["attachment data larger than PidTagAttachSize"] += 1


def check_file(path, name=None):
    """path may be a binary file object, with name for the report."""
    r = {"file": name or os.path.basename(path), "folders": 0, "items": 0, "recipients": 0,
         "attachments": 0, "attached_items": 0, "rtf_bodies": 0, "attachments_without_data": 0,
         "attachment_bytes": 0, "display_list": Counter(), "failures": Counter(),
         "failure_examples": []}
    start = time.monotonic()
    try:
        with pstprobe.open_pst(path) as pst:
            for f in pst.walk_folders():
                r["folders"] += 1
                nids = f.message_nids()
                if (not f.is_search_folder and f.content_count is not None
                        and f.content_count != len(nids)):
                    r["failures"]["folder item count differs from PidTagContentCount"] += 1
                for nid in nids:
                    r["items"] += 1
                    try:
                        m = pst.message(nid)
                        if m is None:
                            r["failures"]["item listed but not found"] += 1
                            continue
                        check_message(m, r, 0)
                    except pstprobe.DAMAGE_ERRORS as e:
                        r["failures"]["item read error: " + type(e).__name__] += 1
                        if len(r["failure_examples"]) < 5:
                            r["failure_examples"].append("0x%x %s: %s" % (nid, type(e).__name__, e))
    except pstprobe.DAMAGE_ERRORS as e:
        r["failures"]["file read error: " + type(e).__name__] += 1
        r["failure_examples"].append("%s: %s" % (type(e).__name__, e))
    r["seconds"] = round(time.monotonic() - start, 1)
    return r


def main():
    bad = 0
    for path in sys.argv[1:]:
        r = check_file(path)
        bad += bool(r["failures"])
        print(json.dumps(r), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
