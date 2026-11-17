"""Record real UI states from the isolated, fictional product demo only."""
from io import BytesIO
from pathlib import Path
import argparse
import re

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8767")
    parser.add_argument("--chrome", default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    args = parser.parse_args()
    frames = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chrome, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page.goto(args.base + "/product/", wait_until="networkidle")
        page.evaluate("document.fonts.ready")

        def capture(name):
            page.wait_for_timeout(350)
            image = Image.open(BytesIO(page.screenshot())).convert("RGB")
            if name:
                image.save(ROOT / f"landing/assets/product-{name}.webp", lossless=True, method=6)
            frames.append(image.resize((960, 600), Image.Resampling.LANCZOS).quantize(colors=128))

        capture("assets")
        page.get_by_role("button", name="Risks", exact=True).click()
        capture("risks")
        page.get_by_text(re.compile("Cross.account access to customer invoices", re.I)).first.click()
        page.get_by_text("Synthetic demonstration: a signed-in user", exact=False).wait_for()
        capture("overview")
        page.get_by_role("tab", name="Evidence").click()
        page.locator(".risks-exchange-chain").wait_for()
        capture("evidence")
        page.locator("summary").filter(has_text="View raw request").click()
        page.locator("summary").filter(has_text="View raw response").click()
        capture(None)
        browser.close()
    frames[0].save(ROOT / "landing/assets/vaayu-demo-workflow.gif", save_all=True, append_images=frames[1:], duration=[1800, 1700, 2200, 2300, 2400], loop=0, optimize=True)
    print("Captured the actual Assets, Risks, Overview, and Evidence UI with synthetic data.")


if __name__ == "__main__":
    main()
