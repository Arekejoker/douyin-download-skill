#!/usr/bin/env python3
"""Backward-compat wrapper: note_media.py <note_id> [...] -> dy.py fetch ...

Old flags (--want/--cdp/--wait) keep working. Output is dy.py's manifest JSON,
which also lands on disk as dy_<id>_manifest.json.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd = [sys.executable, os.path.join(HERE, "dy.py"), "fetch"] + args
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
