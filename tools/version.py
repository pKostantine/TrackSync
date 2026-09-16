"""Version helper for the build scripts.

Batch is poor at capturing a program's output. Every attempt in this project
to do it inline has misfired -- a `for /f` whose command starts with a quote
gets split on the inner quotes and cmd tries to run half of it as a program
name. So the scripts no longer capture anything: this writes a file and
returns an exit code, and batch only has to read a file and test errorlevel,
which it does reliably.

    python tools/version.py                 print the version
    python tools/version.py --write PATH    write it to PATH (no newline)
    python tools/version.py --check PATH    compare PATH against the source
                                            0 = match, 1 = mismatch, 2 = error
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _version():
    import tracksync
    return tracksync.__version__


def main(argv):
    if not argv:
        print(_version())
        return 0

    cmd = argv[0]
    if cmd in ("-h", "--help"):
        print(__doc__)
        return 0

    if len(argv) < 2:
        print(f"version.py: {cmd} needs a path", file=sys.stderr)
        return 2
    path = argv[1]

    if cmd == "--write":
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="ascii", newline="") as fh:
            fh.write(_version())          # no newline: set /p would keep it
        print(_version())
        return 0

    if cmd == "--check":
        try:
            with open(path, encoding="ascii") as fh:
                built = fh.read().strip()
        except OSError as exc:
            print(f"version.py: cannot read {path}: {exc}", file=sys.stderr)
            return 2
        want = _version()
        if built != want:
            print(f"version.py: built={built!r} source={want!r}",
                  file=sys.stderr)
            return 1
        print(built)
        return 0

    print(f"version.py: unknown option {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
