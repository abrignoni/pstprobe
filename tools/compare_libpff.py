"""Compare pstprobe with libpff-python (pypff), used as a comparison oracle only.

Run the oracle dump (tools/oracle_dump.py, in an environment that has pypff) to a
JSON file, then:

    python3 tools/compare_libpff.py file.pst oracle.json

Compares, per folder NID: the number of items; per item NID: the number of
recipients, the number of attachments, and each attachment's data size. Prints
differences only, then a summary. Subjects are not compared or printed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pstprobe  # noqa: E402


def ours(path):
    folders, items = {}, {}
    with pstprobe.open_pst(path) as pst:
        for f in pst.walk_folders():
            nids = f.message_nids()
            folders[f.nid] = len(nids)
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
            if a[k] != b[k]:
                diffs += 1
                print("item 0x%x %s: pstprobe %s, libpff %s" % (nid, k, a[k], b[k]))
        sa = [s for s in a["sizes"]]
        sb = b["sizes"]
        for i, (x, y) in enumerate(zip(sa, sb)):
            if x is not None and x != y:
                diffs += 1
                print("item 0x%x attachment %d size: pstprobe %s, libpff %s" % (nid, i, x, y))
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
