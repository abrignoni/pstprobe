"""Check de-encapsulated RTF bodies against the item's own plain-text body.

    python3 tools/rtf_body_check.py file.pst [more files]

For every item whose PidTagRtfCompressed encapsulates plain text or HTML ([MS-OXRTFEX]) and
that also stores PidTagBody, a second record of the same content:

- plain text: the de-encapsulated text must equal PidTagBody, with CRLF compared as LF and
  trailing line breaks, spaces and NULs set aside;
- RTF that encapsulates nothing (a document written as RTF): the text render_rtf_text gives
  must have the same non-whitespace characters as PidTagBody, as for HTML below. Outlook's
  plain text adds ">" before quoted lines and "*" before list items, which the RTF holds as
  formatting, not text; an item that differs only by those inserted marks is counted under
  "equal_but_marks", not as a difference;
- HTML: the visible text of the de-encapsulated HTML (html.parser, without head, style,
  script and title, entities decoded) must have the same non-whitespace characters as
  PidTagBody, after removing from both the bracketed link targets (<http://...>,
  <name@host>) that a plain-text body adds for each link.

Prints one JSON line per file with counts, and the NIDs of items that differ. No content.
Exit status 1 if any item differs.
"""
import difflib
import html.parser
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pstprobe  # noqa: E402

LINK = re.compile(r"<(?:https?://|mailto:)?[^<>\s]*[@/][^<>\s]*>")


class _Visible(html.parser.HTMLParser):
    HIDDEN = ("head", "style", "script", "title")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in self.HIDDEN and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.out.append(data)


def visible_text(html_text):
    parser = _Visible()
    parser.feed(html_text)
    parser.close()
    return "".join(parser.out)


def _chars(text):
    return "".join(LINK.sub("", text).replace(" ", " ").split())


def _only_marks_added(ours, theirs):
    """True when theirs is ours with only ">" and "*" characters inserted."""
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ours, theirs, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if op != "insert" or set(theirs[j1:j2]) - {">", "*"}:
            return False
    return True


def _lines(text):
    return text.replace("\r\n", "\n").rstrip("\n\0 ")


def check_file(path, name=None):
    """path may be a binary file object, with name for the report."""
    r = {"file": name or os.path.basename(path), "rtf": 0, "encapsulates": Counter(), "compared": Counter(),
         "equal": Counter(), "equal_but_marks": 0, "no_plain_body": 0, "differ": []}
    with pstprobe.open_pst(path) as pst:
        for folder in pst.walk_folders():
            for nid in folder.message_nids():
                m = pst.message(nid)
                if m is None or m.get(pstprobe.PID_TAG_RTF_COMPRESSED) is None:
                    continue
                r["rtf"] += 1
                kind, text = m.rtf_original()
                r["encapsulates"][kind or "nothing"] += 1
                if kind is None:
                    kind = "rtf"
                    text = pstprobe.render_rtf_text(m.rtf_body())[0]
                body = m.body()
                if body is None:
                    r["no_plain_body"] += 1
                    continue
                r["compared"][kind] += 1
                if kind == "text":
                    same = _lines(text) == _lines(body)
                elif kind == "rtf":
                    same = _chars(text) == _chars(body)
                    if not same and _only_marks_added(_chars(text), _chars(body)):
                        r["equal_but_marks"] += 1
                        continue
                else:
                    same = _chars(visible_text(text)) == _chars(body)
                if same:
                    r["equal"][kind] += 1
                else:
                    r["differ"].append("0x%x %s" % (nid, kind))
    return r


def main():
    bad = 0
    for path in sys.argv[1:]:
        r = check_file(path)
        bad += bool(r["differ"])
        print(json.dumps(r), flush=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
