"""Rebuild the public UI demo from a pinned, clean product source checkout.

Only a disposable copy is modified. No production API, credentials, scanning
workers, or customer data are included. The reviewed offline API replaces the
real frontend API module before compilation.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "9cdb493f9eadb404edc17aea16c66b5d51cf8494"


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path, help="Local cyber-jatayu source checkout")
    args = parser.parse_args()
    source = args.source.resolve()
    assert git(source, "rev-parse", "HEAD") == REVISION, "Use the pinned product revision"
    assert not git(source, "status", "--porcelain", "--", "frontend", "prototype/src/styles"), "Product UI source must be clean"
    with tempfile.TemporaryDirectory(prefix="jatayu-public-demo-") as temporary:
        work = Path(temporary)
        shutil.copytree(source / "frontend/src", work / "frontend/src")
        shutil.copytree(source / "prototype/src/styles", work / "prototype/src/styles")
        shutil.copytree(ROOT / "site/demo-overlay", work, dirs_exist_ok=True)
        (work / "public").mkdir()
        shutil.copyfile(ROOT / "landing/assets/vaayu.png", work / "public/vaayu-logo.png")
        # Public punctuation policy, applied only to the disposable copy.
        for path in work.rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx", ".css", ".html"}:
                path.write_text(path.read_text().replace(chr(8212), "-"))
        subprocess.run(["npm", "ci", "--ignore-scripts"], cwd=work, check=True)
        subprocess.run(["npm", "run", "build"], cwd=work, check=True)
        output = ROOT / "landing/product"
        if output.exists():
            shutil.rmtree(output)
        shutil.copytree(work / "dist", output)
    provenance = {
        "source_revision": REVISION,
        "source": "Vaayu product frontend",
        "fixture_source": "site/demo-overlay",
        "network_mode": "offline fixtures only; no production API or target traffic",
        "notes": "Fictional assets and risks; comments are in-memory only; no source maps shipped",
    }
    (ROOT / "site/demo-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print("Built isolated product UI demo. Run site/prepare_static.py next.")


if __name__ == "__main__":
    main()
