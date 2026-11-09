"""Which structure paths a file exercises.

    python3 tools/structure_census.py file.pst

Walks every node in the node B-tree and reports: B-tree depths, data-tree shapes
(single block, XBLOCK, XXBLOCK), subnode-tree shapes (SLBLOCK, SIBLOCK), heaps that
span more than one block, BTH index levels, table row matrices in a subnode and how
many blocks they span, and the largest node. A test on a file only covers a path this
reports as present.
"""
import os
import struct
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pstprobe as P  # noqa: E402


def tree_shape(pst, bid):
    if not bid:
        return "none"
    if not bid & 2:
        return "block"
    b = pst.read_block(bid)
    btype, level = b[0], b[1]
    return {(1, 1): "XBLOCK", (1, 2): "XXBLOCK", (2, 0): "SLBLOCK", (2, 1): "SIBLOCK"}.get(
        (btype, level), "btype%d/level%d" % (btype, level))


def bt_depth(pst, root_ib, ptype):
    level, _, _, _ = pst._page(root_ib, ptype)
    return level + 1


def main(path):
    c = Counter()
    largest = 0
    with P.open_pst(path) as pst:
        c["NBT levels %d" % bt_depth(pst, pst.nbt_root[1], P.PTYPE_NBT)] += 1
        c["BBT levels %d" % bt_depth(pst, pst.bbt_root[1], P.PTYPE_BBT)] += 1

        def visit(node, top):
            nonlocal largest
            c["data " + tree_shape(pst, node.bid_data)] += 1
            if node.bid_sub:
                c["subnodes " + tree_shape(pst, node.bid_sub)] += 1
            size = sum(len(pst.read_block(b)) for b in node.blocks)
            largest = max(largest, size)
            if not node.blocks:
                return
            b0 = node.block(0)
            if len(b0) >= 8 and b0[2] == P.HN_SIG:
                c["heap blocks %s" % ("1" if len(node.blocks) == 1 else ">1")] += 1
                heap = P.Heap(node)
                if heap.client_sig in (P.CLIENT_SIG_PC, P.CLIENT_SIG_TC):
                    root_hid = heap.user_root
                    if heap.client_sig == P.CLIENT_SIG_TC:
                        tc = P.TableContext(node)
                        root_hid = tc.hid_row_index
                        if tc.hnid_rows and tc.hnid_rows & 0x1F:
                            rn = node.subnode(tc.hnid_rows)
                            c["TC rows in subnode, %s block(s)" % ("1" if len(rn.blocks) == 1 else ">1")] += 1
                        elif tc.hnid_rows:
                            c["TC rows in heap"] += 1
                    if root_hid:
                        hdr = heap.get(root_hid)
                        c["BTH index levels %d" % hdr[3]] += 1
            for nid, (bd, bs) in node.subnodes.items():
                visit(P.Node(pst, nid, bd, bs, node.nid), False)

        for e in pst.iter_nbt():
            visit(P.Node(pst, e.nid, e.bid_data, e.bid_sub, e.nid_parent), True)
    for k in sorted(c):
        print("%-40s %d" % (k, c[k]))
    print("%-40s %d" % ("largest node data (bytes)", largest))


if __name__ == "__main__":
    main(sys.argv[1])
