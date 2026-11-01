#!/usr/bin/env python3
"""Build (or verify) releases/cad2llm_exporter.rbz from src/.

The archive is a plain zip with a fixed entry order and a fixed timestamp, so a
rebuild is byte-identical when src/ did not change. That is the only way to
prove the shipped .rbz really contains the reviewed code.

    python -X utf8 tools/build_rbz.py            # rebuild
    python -X utf8 tools/build_rbz.py --check    # compare only, exit 1 on drift
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
RBZ = ROOT / "releases" / "cad2llm_exporter.rbz"
STAMP = (2026, 10, 27, 0, 0, 0)

# Same order as the archive has always had.
MEMBERS = [
    ("cad2llm_exporter.rb", SRC / "cad2llm_exporter.rb"),
    ("cad2llm_exporter/main.rb", SRC / "cad2llm_exporter" / "main.rb"),
    (
        "cad2llm_exporter/lib/exporter.rb",
        SRC / "cad2llm_exporter" / "lib" / "exporter.rb",
    ),
    (
        "cad2llm_exporter/lib/annotate.rb",
        SRC / "cad2llm_exporter" / "lib" / "annotate.rb",
    ),
    ("cad2llm_exporter/lib/bridge.rb", SRC / "cad2llm_exporter" / "lib" / "bridge.rb"),
    (
        "cad2llm_exporter/html/inspector.html",
        SRC / "cad2llm_exporter" / "html" / "inspector.html",
    ),
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check() -> int:
    if not RBZ.is_file():
        print(f"CHYBA: chybi {RBZ}")
        return 1
    bad = 0
    with zipfile.ZipFile(RBZ) as archive:
        names = archive.namelist()
        expected = [name for name, _ in MEMBERS]
        if names != expected:
            print(f"CHYBA: poradi/cleneni archivu se lisi: {names}")
            bad += 1
        for name, path in MEMBERS:
            if name not in names:
                print(f"CHYBA: {name} v archivu chybi")
                bad += 1
                continue
            packed = archive.read(name)
            source = path.read_bytes()
            if packed != source:
                print(
                    f"DRIFT: {name} src={digest(source)[:12]} rbz={digest(packed)[:12]}"
                )
                bad += 1
            else:
                print(f"ok    {name} sha256={digest(source)[:12]}")
    return 0 if not bad else 1


def build() -> int:
    missing = [str(path) for _, path in MEMBERS if not path.is_file()]
    if missing:
        print("CHYBA: chybi zdroje: " + ", ".join(missing))
        return 1
    RBZ.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(RBZ, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in MEMBERS:
            info = zipfile.ZipInfo(name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    print(f"zapsano {RBZ} ({RBZ.stat().st_size} B, {len(MEMBERS)} souboru)")
    return check()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="build/verify cad2llm_exporter.rbz")
    ap.add_argument("--check", action="store_true", help="jen porovnat src a rbz")
    args = ap.parse_args(argv)
    return check() if args.check else build()


if __name__ == "__main__":
    sys.exit(main())
