"""Compare pstprobe with libpff-python (pypff), used as a comparison oracle only.

Run the oracle dump (tools/oracle_dump.py, in an environment that has pypff) to a
JSON file, then:

    python3 tools/compare_libpff.py file.pst oracle.json

Compares, per folder NID: the number of items; per item NID: the number of
recipients, the number of attachments, and each attachment's data size. Prints
differences only, then a summary. Subjects are not compared or printed.

libpff returns some item types (contacts, appointments) as objects without recipients
or attachments; those values are counted as not compared, not as differences. Each
folder's item count is also checked against the folder's own PidTagContentCount, a
second record of the same fact that does not depend on libpff.
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pstprobe  # noqa: E402


CONTENT_COUNT_MISMATCHES = []


def ours(path):
    folders, items = {}, {}
    with pstprobe.open_pst(path) as pst:
        for f in pst.walk_folders():
            nids = f.message_nids()
            folders[f.nid] = len(nids)
            if not f.is_search_folder and f.content_count is not None and f.content_count != len(nids):
                CONTENT_COUNT_MISMATCHES.append((f.nid, f.content_count, len(nids)))
            for nid in nids:
                m = pst.message(nid)
                if m is None:
                    items[nid] = "missing"
                    continue
                atts = m.attachments()
                sizes = []
                for a in atts:
                    sizes.append(None if a.is_embedded_message else a.data_size())
                items[nid] = {"recipients": len(m.recipients()), "attachments": len(atts),
                              "sizes": sizes}
    return folders, items


def theirs(path):
    folders, items = {}, {}
    for f in json.load(open(path)):
        folders[f["id"]] = f["n_messages"]
        for m in f["messages"]:
            items[m.get("id")] = {"recipients": m["recipients"], "attachments": m["attachments"],
                                  "sizes": [a.get("size") for a in m.get("att", [])]}
    return folders, items


def main():
    of, oi = ours(sys.argv[1])
    tf, ti = theirs(sys.argv[2])
    diffs = 0
    not_compared = 0
    for nid in sorted(set(of) | set(tf)):
        if of.get(nid) != tf.get(nid):
            diffs += 1
            print("folder 0x%x: pstprobe %s, libpff %s" % (nid, of.get(nid), tf.get(nid)))
    for nid in sorted(set(oi) | set(ti), key=lambda x: x or 0):
        a, b = oi.get(nid), ti.get(nid)
        if a is None or b is None:
            diffs += 1
            print("item 0x%x: pstprobe %s, libpff %s" % (nid, a, b))
            continue
        for k in ("recipients", "attachments"):
            if isinstance(b[k], str) and b[k].startswith("ERR"):
                not_compared += 1
                continue
            if a[k] != b[k]:
                diffs += 1
                print("item 0x%x %s: pstprobe %s, libpff %s" % (nid, k, a[k], b[k]))
        # Compared as a multiset: pstprobe lists attachments in the attachment table's row
        # order, libpff in another, and the file records no order of its own for them. An
        # attached item has no data size in pstprobe (None), so only file attachments count;
        # each of their sizes must be among libpff's.
        mine = Counter(x for x in a["sizes"] if x is not None)
        theirs_sizes = Counter(y for y in b["sizes"] if isinstance(y, int))
        if mine - theirs_sizes:
            diffs += 1
            print("item 0x%x attachment sizes: pstprobe %s, libpff %s"
                  % (nid, sorted(mine.elements()), sorted(theirs_sizes.elements())))
    for nid, stored, listed in CONTENT_COUNT_MISMATCHES:
        diffs += 1
        print("folder 0x%x: PidTagContentCount %d, contents table lists %d" % (nid, stored, listed))
    print("not compared (libpff gives no value for that item type): %d" % not_compared)
    print("folders: pstprobe %d, libpff %d; items: pstprobe %d, libpff %d; "
          "recipients: pstprobe %d, libpff %d; attachments: pstprobe %d, libpff %d; differences: %d"
          % (len(of), len(tf), len(oi), len(ti),
             sum(v["recipients"] for v in oi.values() if isinstance(v, dict)),
             sum(v["recipients"] for v in ti.values() if isinstance(v["recipients"], int)),
             sum(v["attachments"] for v in oi.values() if isinstance(v, dict)),
             sum(v["attachments"] for v in ti.values() if isinstance(v["attachments"], int)), diffs))
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
