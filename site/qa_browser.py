"""Browser QA for the static site and isolated, fictional product demonstration.

Requires Playwright in the development environment. Writes review artifacts to
/tmp/jatayu-site-qa by default. Never initiates a scan or sends a demo request.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8767")
    parser.add_argument("--chrome", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    parser.add_argument("--axe", default="/tmp/jatayu-axe.min.js")
    parser.add_argument("--output", default="/tmp/jatayu-site-qa")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    errors = []
    outbound = []
    allowed = {urlsplit(args.base).hostname}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chrome, headless=True)
        for width, height in ((1440, 1000), (1280, 800), (1024, 768), (900, 900), (768, 900), (390, 844), (320, 720), (1440, 700)):
            for motion in ("reduce", "no-preference"):
                page = browser.new_page(viewport={"width": width, "height": height}, reduced_motion=motion)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                page.on("request", lambda request: outbound.append(request.url) if urlsplit(request.url).hostname not in allowed and urlsplit(request.url).scheme in {"http", "https"} else None)
                page.goto(args.base + "/", wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                page.wait_for_timeout(1000)
                overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
                assert not overflow, (width, height, motion)
                assert 'Inter' in page.evaluate("getComputedStyle(document.body).fontFamily")
                assert page.evaluate("document.fonts.check('400 15px Inter')")
                assert page.locator("h1").inner_text().replace("\n", " ").startswith("From attack surface")
                assert page.locator("form").count() == 0
                assert page.locator(".preview-switcher").count() == 0
                assert page.locator(".demo-email-link").get_attribute("href").startswith("mailto:founders@jatayulabs.com?subject=Vaayu%20demo%20request")
                if width == 1440 and motion == "no-preference":
                    pause = page.locator(".hero-motion-toggle")
                    pause.click()
                    current = page.locator(".hero-frame.active").get_attribute("data-hero-frame")
                    page.wait_for_timeout(4500)
                    assert current == page.locator(".hero-frame.active").get_attribute("data-hero-frame")
                    pause.click()
                if width >= 1024:
                    for index, chapter in enumerate(("chapter-assets", "chapter-risks", "chapter-impact", "chapter-evidence")):
                        page.locator("#" + chapter).scroll_into_view_if_needed()
                        page.wait_for_timeout(700)
                        assert page.locator(".story-screen").get_attribute("data-current") == str(index)
                page.locator('a[href="#demo"]').first.click()
                page.wait_for_timeout(500)
                assert page.locator(".demo-email-link").is_visible()
                page.locator("summary").last.click()
                assert page.get_by_text("No. A demo request is not authorization", exact=False).is_visible()
                page.evaluate("scrollTo(0,0)")
                page.wait_for_timeout(700)
                if motion == "reduce":
                    page.screenshot(path=str(output / f"site-{width}-{height}.png"))
                    if width in (1440, 390):
                        page.screenshot(path=str(output / f"site-{width}-{height}-full.png"), full_page=True)
                if width in (1440, 390) and motion == "reduce" and Path(args.axe).is_file():
                    page.evaluate(Path(args.axe).read_text())
                    audit = page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                    (output / f"axe-{width}.json").write_text(json.dumps(audit, indent=2))
                    violations = [{"id": item["id"], "impact": item["impact"], "targets": [node["target"] for node in item["nodes"]]} for item in audit["violations"]]
                else:
                    violations = []
                results.append({"width": width, "height": height, "motion": motion, "overflow": overflow, "accessibility_violations": violations})
                page.close()

        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
        page.on("request", lambda request: outbound.append(request.url) if urlsplit(request.url).hostname not in allowed and urlsplit(request.url).scheme in {"http", "https"} else None)
        page.goto(args.base + "/product/", wait_until="networkidle")
        page.get_by_text("api.northstar.example", exact=True).first.click()
        page.locator('.assets-drawer-tabs button').filter(has_text='Risks').click()
        page.get_by_text("Cross-account access to customer invoices", exact=True).first.click()
        page.get_by_role("tab", name="Evidence").click()
        assert page.get_by_text("GET", exact=True).first.is_visible()
        page.get_by_role("button", name="Raw").first.click()
        assert page.get_by_text("HTTP/1.1 200 OK", exact=False).first.is_visible()
        page.get_by_role("tab", name="Notes").click()
        page.get_by_role("textbox", name="Add a comment").fill("Synthetic demo review note")
        page.get_by_role("button", name="Comment", exact=True).click()
        assert page.get_by_text("Synthetic demo review note", exact=True).is_visible()
        page.reload(wait_until="networkidle")
        assert page.get_by_text("Synthetic demo review note", exact=True).count() == 0
        page.screenshot(path=str(output / "product-1440.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(args.base + "/product/?view=risks", wait_until="networkidle")
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth + 1")
        page.screenshot(path=str(output / "product-390.png"))
        page.close()
        browser.close()
    report = {"viewports": results, "console_or_page_errors": errors, "outbound_requests": outbound, "demo_workflow": "asset to risk to raw evidence to in-memory notes; reload clears notes"}
    (output / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    assert not errors, errors
    assert not outbound, outbound
    assert not any(item["accessibility_violations"] for item in results), "Accessibility violations; see review artifacts"


if __name__ == "__main__":
    main()
