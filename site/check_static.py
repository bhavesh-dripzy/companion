"""Fail closed on missing assets, preview controls, remote scripts, or fake forms."""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "landing"


class Document(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.references: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.forms = 0
        self.inline_scripts = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if "id" in values:
            value = str(values["id"])
            assert value not in self.ids, f"Duplicate id: {value}"
            self.ids.add(value)
        for attribute in ("src", "href"):
            if values.get(attribute):
                self.references.append((tag, str(values[attribute])))
        if tag == "form":
            self.forms += 1
        if tag == "script":
            self.scripts.append(str(values.get("src") or ""))
            self.inline_scripts += not bool(values.get("src"))


def main() -> None:
    manifest = json.loads((PUBLIC / "release-manifest.json").read_text())
    for item in manifest["files"]:
        path = PUBLIC / item["path"]
        body = path.read_bytes()
        assert len(body) == item["bytes"], path
        assert hashlib.sha256(body).hexdigest() == item["sha256"], path
    for path in PUBLIC.rglob("*"):
        if path.is_file() and path.suffix in {".html", ".css", ".js", ".txt", ".xml", ".json"}:
            assert chr(8212) not in path.read_text(), f"Disallowed punctuation: {path}"
    assert (ROOT / "site/source/site.css").read_text().lstrip().startswith(":root"), "Missing base typography rule"
    for path in PUBLIC.rglob("*.html"):
        source = path.read_text()
        document = Document()
        document.feed(source)
        assert not document.inline_scripts, f"Inline script rejected: {path}"
        assert not document.forms, f"Unconfigured form rejected: {path}"
        assert "preview-switcher" not in source, path
        assert chr(8212) not in source, path
        for tag, reference in document.references:
            parsed = urlsplit(reference)
            if tag == "script":
                assert not parsed.netloc and parsed.scheme == "", reference
            if parsed.scheme or parsed.netloc:
                continue
            if reference.startswith("#"):
                assert reference[1:] in document.ids, reference
                continue
            target = PUBLIC / parsed.path.lstrip("/") if parsed.path.startswith("/") else path.parent / parsed.path
            if parsed.path.endswith("/"):
                target /= "index.html"
            assert target.is_file(), f"Missing asset {reference} in {path}"
    home = (PUBLIC / "index.html").read_text()
    assert "Get demo" in home and "mailto:founders@jatayulabs.com" in home
    assert "waitlist" not in home.lower()
    assert "Local design preview" not in home
    assert '<link rel="canonical" href="https://jatayulabs.com/">' in home
    for path in PUBLIC.rglob("*.css"):
        css = path.read_text()
        assert not re.search(r"@import|url\(['\"]?https?://", css), path
    assert (PUBLIC / "product" / "index.html").is_file()
    assert 'content="noindex,nofollow"' in (PUBLIC / "product" / "index.html").read_text()
    print(f"Verified {len(manifest['files'])} release files, local dependencies, and production contact flow.")


if __name__ == "__main__":
    main()
