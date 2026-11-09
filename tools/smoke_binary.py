"""Check a frozen pstprobe build against the source it was frozen from.

    python tools/smoke_binary.py --cli dist/pstprobe [--gui PATH] [--version 0.2.0] [FILE ...]

The CLI binary must print the expected version and give the same output as `python pstprobe.py`
for info, folders, items, props and export, on a PST built by tests/pstbuilder.py and on any
FILE given. The window binary must pass --selfcheck on the built PST. A windowed Windows build
has no stdout, so only its exit code is checked there. Run it from the repository root; on
Linux, run it under a virtual display.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tests"))
import pstbuilder  # noqa: E402  pylint: disable=wrong-import-position,import-error

FAILED = []


def check(ok, what):
    print(("ok    " if ok else "FAIL  ") + what)
    if not ok:
        FAILED.append(what)


def run(cmd):
    done = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, check=False,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def tree(folder):
    """Every file below folder with its bytes, for comparing two exports."""
    out = {}
    for base, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, folder)] = handle.read()
    return out


def compare(source, binary, path, label, work):
    for sub in (["info", path], ["folders", path], ["items", "--subjects", path], ["props", path, "0x21"]):
        want, got = run(source + sub), run(binary + sub)
        check(want[0] == 0 and got == want, "%s: %s matches the source%s" % (
            label, sub[0], "" if got == want else " (exit %d vs %d; %s)" % (got[0], want[0], got[2].strip()[-300:])))
    a, b = os.path.join(work, label + "-src"), os.path.join(work, label + "-bin")
    want = run(source + ["export", path, "--recursive", "-o", a])
    got = run(binary + ["export", path, "--recursive", "-o", b])
    same = want[0] == 0 and got[0] == 0 and tree(a) == tree(b)
    check(same, "%s: export --recursive writes the same files as the source (%s)" % (label, got[1].strip()[:120]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--gui")
    parser.add_argument("--version")
    parser.add_argument("files", nargs="*")
    args = parser.parse_args()
    source = [sys.executable, os.path.join(HERE, "pstprobe.py")]
    binary = [os.path.abspath(args.cli)]
    with tempfile.TemporaryDirectory() as work:
        sample = os.path.join(work, "sample.pst")
        with open(sample, "wb") as handle:
            handle.write(pstbuilder.build_sample("unicode", 1)[0])
        code, out, err = run(binary + ["--version"])
        expected = run(source + ["--version"])[1].strip()
        check(code == 0 and out.strip() == expected, "--version prints %r (got %r%s)" % (expected, out.strip(), err.strip()))
        if args.version:
            check(out.strip() == "pstprobe %s" % args.version, "version is %s" % args.version)
        compare(source, binary, sample, "sample", work)
        for n, path in enumerate(args.files):
            compare(source, binary, os.path.abspath(path), "file%d" % n, work)
        if args.gui:
            code, out, err = run([os.path.abspath(args.gui), "--selfcheck", sample])
            check(code == 0, "window --selfcheck exits 0 (got %d; %s)" % (code, err.strip()[-300:]))
            if out.strip():
                report = json.loads(out)
                check(report.get("window") == "built" and report.get("items_shown") == 1
                      and report.get("attachment_files_saved") == 1, "window loaded the file: %s" % report)
            else:
                print("note  the window printed nothing (a windowed Windows build has no stdout)")
            for path in args.files:
                code, out, err = run([os.path.abspath(args.gui), "--selfcheck", os.path.abspath(path)])
                check(code == 0, "window --selfcheck on %s exits 0 (%s)" % (os.path.basename(path), out.strip()[:200]))
    print("%d failed" % len(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
