"""Build the company site from reviewed sources; no API or credential inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "site" / "source"
PUBLIC = ROOT / "landing"


def fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    assets = PUBLIC / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    outputs = {}
    # Clean only generated site bundles locally. Previously published immutable
    # objects remain in the versioned bucket so cached pages can still load.
    for pattern in ("site-*.css", "site-*.js"):
        for old in assets.glob(pattern):
            old.unlink()
    for name, suffix in (("site", "css"), ("site", "js")):
        content = (SOURCE / f"{name}.{suffix}").read_bytes()
        filename = f"{name}-{fingerprint(content)[:16]}.{suffix}"
        (assets / filename).write_bytes(content)
        outputs[suffix] = f"/assets/{filename}"
    template = (SOURCE / "index.html").read_text()
    template = template.replace("{{SITE_CSS}}", outputs["css"])
    template = template.replace("{{SITE_JS}}", outputs["js"])
    if "{{SITE_" in template:
        raise RuntimeError("Unresolved production asset placeholder")
    (PUBLIC / "index.html").write_text(template)
    records = []
    for path in sorted(PUBLIC.rglob("*")):
        if path.is_file() and path.name != "release-manifest.json":
            body = path.read_bytes()
            records.append({"path": str(path.relative_to(PUBLIC)), "sha256": fingerprint(body), "bytes": len(body)})
    manifest = {"schema_version": "jatayu-site-release/v1", "domain": "jatayulabs.com", "files": records}
    (PUBLIC / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Built {len(records)} production files.")


if __name__ == "__main__":
    main()
