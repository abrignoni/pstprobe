"""Dump folder tree and per-item counts with libpff (pypff), comparison oracle only."""
import json, sys, pypff

def att_info(a):
    try:
        return {"size": a.get_size()}
    except Exception as e:
        return {"error": type(e).__name__}

def msg(m):
    d = {"subject": None, "recipients": None, "attachments": None}
    try: d["subject"] = m.get_subject()
    except Exception as e: d["subject"] = "ERR " + type(e).__name__
    try:
        rc = m.recipients
        d["recipients"] = rc.get_number_of_record_sets() if rc is not None else 0
        d["recipients_alt"] = rc.get_number_of_recipients() if rc is not None else 0
    except Exception as e: d["recipients"] = "ERR " + type(e).__name__
    try:
        n = m.get_number_of_attachments(); d["attachments"] = n
        d["att"] = [att_info(m.get_attachment(i)) for i in range(n)]
    except Exception as e: d["attachments"] = "ERR"
    try: d["msg_class"] = m.get_message_class()
    except Exception: pass
    try: d["id"] = m.get_identifier()
    except Exception: pass
    return d

def folder(f, path):
    name = f.get_name() or ""
    p = path + "/" + name
    out = {"path": p, "id": f.get_identifier(), "n_messages": f.get_number_of_sub_messages(),
           "n_subfolders": f.get_number_of_sub_folders(), "messages": []}
    for i in range(f.get_number_of_sub_messages()):
        try: out["messages"].append(msg(f.get_sub_message(i)))
        except Exception as e: out["messages"].append({"error": str(e)[:80]})
    res = [out]
    for i in range(f.get_number_of_sub_folders()):
        res += folder(f.get_sub_folder(i), p)
    return res

pf = pypff.file(); pf.open(sys.argv[1])
res = folder(pf.get_root_folder(), "")
json.dump(res, open(sys.argv[2], "w"), indent=1)
print("folders", len(res), "messages", sum(r["n_messages"] for r in res))
for r in res: print(r["id"], r["n_messages"], r["n_subfolders"], r["path"].encode("ascii","replace").decode())
