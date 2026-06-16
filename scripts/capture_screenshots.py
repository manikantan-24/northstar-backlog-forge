"""Capture fresh screenshots for docs/screenshots/.

Usage
-----
1. Start the app in one terminal:
       make ui          (or: streamlit run app.py)

2. In another terminal:
       pip install playwright
       playwright install chromium
       python scripts/capture_screenshots.py

Outputs (overwrites existing files):
    docs/screenshots/01_home.png
    docs/screenshots/02_pipeline.png
    docs/screenshots/03_epics.png
    docs/screenshots/04_findings.png
    docs/screenshots/05_audit.png

Optional GIF (requires ffmpeg):
    docs/screenshots/demo.gif
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS_DIR = ROOT / "docs" / "screenshots"
SAMPLES_DIR = ROOT / "samples"
APP_URL = "http://localhost:8501"

VIEWPORT = {"width": 1680, "height": 1050}
TIMEOUT = 300_000   # 5 min — synthesis can take a while

# ── helpers ────────────────────────────────────────────────────────────────────

def _wait_for_pipeline_done(page) -> None:
    """Block until all 5 stage chips show 'done' or an error appears."""
    print("  waiting for pipeline to complete (up to 5 min)…")
    page.wait_for_selector(
        "text=Audit trail",
        timeout=TIMEOUT,
        state="visible",
    )
    time.sleep(2)  # let Streamlit finish re-rendering after tabs appear


def _upload_file(page, section_label: str, file_path: Path) -> None:
    """Upload a file via the named expander section's file uploader."""
    uploader = page.get_by_label(section_label).first
    if uploader.count() == 0:
        return
    uploader.set_input_files(str(file_path))
    time.sleep(0.5)


def _click_synthesize(page) -> None:
    page.get_by_role("button", name="SYNTHESIZE").last.click()


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        sys.exit(
            "playwright not installed.\n"
            "Run: pip install playwright && playwright install chromium"
        )

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            channel="chrome",   # uses system Chrome if available
            headless=False,     # set True for headless capture
            slow_mo=150,
        )
        ctx = browser.new_context(viewport=VIEWPORT)
        page = ctx.new_page()
        page.set_default_timeout(TIMEOUT)

        # ── 01_home.png — landing / empty state ───────────────────────────────
        print("→ 01_home.png  (landing page)")
        page.goto(APP_URL, wait_until="networkidle")
        time.sleep(3)
        page.screenshot(
            path=str(SHOTS_DIR / "01_home.png"),
            full_page=False,
        )
        print("   saved 01_home.png")

        # ── Load sample files into the sidebar ────────────────────────────────
        # Try to upload the bundled samples so the run is self-contained.
        transcript = SAMPLES_DIR / "meeting_notes.txt"
        constraints = SAMPLES_DIR / "architecture_constraints.md"
        backlog = SAMPLES_DIR / "jira_backlog.json"

        for label, path in [
            ("Upload your own", transcript),
            ("Upload your own", constraints),
            ("Upload your own", backlog),
        ]:
            try:
                locator = page.locator('input[type="file"]')
                if locator.count() > 0:
                    locator.first.set_input_files(str(path))
                    time.sleep(0.8)
            except Exception:
                pass

        # ── Click Synthesize + capture mid-run ────────────────────────────────
        print("→ 02_pipeline.png  (mid-run — click SYNTHESIZE)")
        try:
            _click_synthesize(page)
        except Exception:
            # Fallback: the main-area button
            page.get_by_role("button", name="SYNTHESIZE").click()

        # Wait briefly then capture the pipeline in-progress state
        time.sleep(4)
        page.screenshot(
            path=str(SHOTS_DIR / "02_pipeline.png"),
            full_page=False,
        )
        print("   saved 02_pipeline.png")

        # ── Wait for run to finish ─────────────────────────────────────────────
        print("→ waiting for synthesis to complete…")
        try:
            _wait_for_pipeline_done(page)
        except PWTimeout:
            sys.exit(
                "\n  Timed out waiting for synthesis to complete.\n"
                "  Make sure the app is running and ANTHROPIC_API_KEY is set."
            )

        # ── 03_epics.png — Epics tab ──────────────────────────────────────────
        print("→ 03_epics.png  (Epics tab)")
        try:
            page.get_by_role("tab", name="Epics").click()
            time.sleep(1.5)
            # Expand the first epic
            epic_expand = page.locator("[data-testid='stExpander']").first
            if epic_expand.count():
                epic_expand.click()
                time.sleep(1)
        except Exception:
            pass
        page.screenshot(
            path=str(SHOTS_DIR / "03_epics.png"),
            full_page=False,
        )
        print("   saved 03_epics.png")

        # ── 04_findings.png — Gaps tab ────────────────────────────────────────
        print("→ 04_findings.png  (Gaps / findings tab)")
        try:
            page.get_by_role("tab", name="Gaps").click()
            time.sleep(1.5)
        except Exception:
            try:
                # Fallback: find first tab that contains "Gaps"
                page.locator("button[role='tab']", has_text="Gaps").click()
                time.sleep(1.5)
            except Exception:
                pass
        page.screenshot(
            path=str(SHOTS_DIR / "04_findings.png"),
            full_page=False,
        )
        print("   saved 04_findings.png")

        # ── 05_audit.png — Audit trail tab ───────────────────────────────────
        print("→ 05_audit.png  (Audit trail tab)")
        try:
            page.get_by_role("tab", name="Audit trail").click()
            time.sleep(1.5)
            # Expand the first audit entry
            audit_expand = page.locator("[data-testid='stExpander']").first
            if audit_expand.count():
                audit_expand.click()
                time.sleep(1)
        except Exception:
            pass
        page.screenshot(
            path=str(SHOTS_DIR / "05_audit.png"),
            full_page=False,
        )
        print("   saved 05_audit.png")

        browser.close()

    print("\n✓  All screenshots saved to docs/screenshots/")
    print("   Review them, then: git add docs/screenshots/ && git commit -m 'docs: refresh screenshots'")


if __name__ == "__main__":
    main()
