"""Exercise the developer data browser with synthetic, complete evidence."""

from __future__ import annotations

import io
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

from openassist.core.activity import Actor, CollectionLevel, Observation, ObservationStore
from openassist.runtime.activity.browser import ActivityBrowserRuntime
from openassist.runtime.activity.monitor import build_test_config


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    artifacts = root / ".artifacts" / "activity-browser-verification"
    artifacts.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    findings: list[str] = []
    with tempfile.TemporaryDirectory() as directory:
        data_root = Path(directory)
        config = replace(build_test_config(data_root, content=True, persist_images=True),
                         database_path=data_root / "observations.sqlite3",
                         asset_directory=data_root / "assets")
        with ObservationStore(config.database_path, asset_directory=config.asset_directory) as source:
            sequence = 0

            def append(kind: str, at: int, **fields):
                nonlocal sequence
                observation = Observation.create(kind=kind, source_id="browser-fixture", source_epoch="epoch",
                    source_seq=sequence, boot_id="boot", observed_at_utc_ms=at,
                    app_id="fixture.exe", window_instance_id="fixture-window",
                    collection_level=CollectionLevel.CONTENT, **fields)
                sequence += 1
                return source.append(observation)

            for index in range(60):
                append("window_context", 1_700_000_000_000 + index,
                       payload={"window_title": f"Fixture page {index}", "browser_url": "https://fixture.invalid/page"})
            image = Image.new("RGB", (640, 320), "white")
            ImageDraw.Draw(image).text((24, 24), "Synthetic captured page / 12345", fill="black")
            image_bytes = io.BytesIO()
            image.save(image_bytes, format="PNG")
            reservation = source.reserve_asset(mime_type="image/png", scope="activity-content", policy_version=1)
            frame = append("content_snapshot", 1_700_000_000_100,
                payload={"trigger": "typing_pause", "scope": "foreground"}, asset_refs=(reservation.asset_id,),
                visual_summary={"status": "ok", "capture_scope": "foreground"})
            source.commit_asset_bytes(reservation, image_bytes.getvalue(), frame)
            long_text = "Complete OCR beginning\n" + "Useful body content 1234567890\n" * 1_500 + "\nComplete OCR tail"
            append("ocr_result", 1_700_000_000_101, caused_by=(frame.observation_id,),
                   ocr_summary={"status": "ok", "text": long_text})
            batch = append("input_batch", 1_700_000_000_102, actor=Actor.USER, actor_basis="physical_input",
                           payload={"keyboard_count": 5, "click_count": 1})
            append("input_text", 1_700_000_000_103, actor=Actor.USER, actor_basis="physical_input",
                   caused_by=(batch.observation_id,), payload={"inserted_text": "Fixture user input", "baseline_known": True})
        runtime = ActivityBrowserRuntime(config, root=root)
        try:
            url = runtime.start()
            api_root = url.split("/?", 1)[0]
            headers = {"Authorization": "Bearer " + runtime.token}

            def get(path: str):
                with urlopen(Request(api_root + path, headers=headers), timeout=10) as response:
                    return json.loads(response.read())

            for endpoint in ("/api/browser/observations", "/api/knowledge/items", "/api/knowledge/episodes",
                             "/api/knowledge/evidence", "/api/knowledge/events", "/api/knowledge/timeline",
                             "/api/knowledge/context", "/api/knowledge/summary", "/api/knowledge/health",
                             "/api/knowledge/memory/candidates", "/api/knowledge/memory/facts"):
                try:
                    get(endpoint + "?limit=25&tokens=100000&include_content=1&include_uia=1&include_ocr=1")
                except HTTPError as error:
                    findings.append(f"HTTP {error.code}: {endpoint}: {error.read().decode()}")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="msedge", headless=True)
                try:
                    page = browser.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    for width, height in ((1320, 900), (1680, 1000), (390, 844)):
                        page.set_viewport_size({"width": width, "height": height})
                        page.goto(url)
                        page.wait_for_timeout(500)
                        for tab in ("raw", "items", "episodes", "evidence", "events", "timeline", "context",
                                    "summary", "candidates", "facts", "diagnostics"):
                            page.locator(f"button[data-tab='{tab}']").click()
                            page.wait_for_timeout(250)
                            status = page.locator("#data-status").inner_text()
                            if status == "error":
                                findings.append(f"{width}px {tab}: {page.locator('#notice').inner_text()}")
                            if page.evaluate("document.documentElement.scrollWidth > innerWidth"):
                                findings.append(f"{width}px {tab}: document overflows horizontally")
                        page.locator("button[data-tab='raw']").click()
                        page.wait_for_timeout(250)
                        page.screenshot(path=str(artifacts / f"browser-{width}x{height}.png"), full_page=True)
                        if not page.locator("#next").is_disabled():
                            first = page.locator("#tbody tr[data-id]").first.get_attribute("data-id")
                            page.locator("#next").click()
                            page.wait_for_timeout(250)
                            second = page.locator("#tbody tr[data-id]").first.get_attribute("data-id")
                            if first == second:
                                findings.append(f"{width}px raw: next page repeats initial row")
                    page.locator("button[data-tab='raw']").click()
                    page.locator("#kind").fill("content_snapshot")
                    page.locator("#apply").click()
                    page.wait_for_timeout(500)
                    if page.locator("#tbody tr[data-id]").count():
                        page.locator("#tbody tr[data-id]").first.click()
                        page.wait_for_timeout(500)
                        if not page.evaluate("document.getElementById('asset').naturalWidth > 0"):
                            findings.append("asset image did not render")
                    page.locator("#kind").fill("ocr_result")
                    page.locator("#apply").click()
                    page.wait_for_timeout(500)
                    if "Complete OCR tail" not in page.locator("#detail").inner_text():
                        findings.append("raw detail did not preserve complete OCR tail")
                finally:
                    browser.close()
        finally:
            runtime.close()
    report = {"javascript_errors": errors, "findings": findings, "screenshots": str(artifacts)}
    print(json.dumps(report, indent=2))
    return 1 if errors or findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
