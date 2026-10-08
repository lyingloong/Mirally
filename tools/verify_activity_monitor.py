"""Screenshot and exercise the monitor with synthetic data only."""

import io
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from openassist.core.activity.models import CollectionLevel
from openassist.runtime.activity.monitor import ActivityMonitorRuntime, build_test_config


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    artifacts = root/".artifacts"/"activity-ui-verification"
    artifacts.mkdir(parents=True, exist_ok=True)
    config = replace(build_test_config(artifacts), input_enabled=False, ui_structure_enabled=False,
                     visual_enabled=False, ocr_enabled=False, browser_urls_enabled=False)
    runtime = ActivityMonitorRuntime(config,root=root)
    runtime.sensor.source = type("EmptySource", (), {"available":False, "poll":lambda self:None})()
    runtime.sensor.visual_source = type("SyntheticCapture", (), {
        "available": True,
        "status": lambda self: {"available":True,"running":False,"helper_pid":None,
            "session_count":0,"session_id":1,"session_reused":True,"frame_id":42,
            "resize_count":1,"readbacks":3,"persistent":True,"device_driver":"hardware",
            "border_status":"disable_requested","border_hresult":0},
    })()
    errors = []
    try:
        image = Image.new("RGB", (640,320), "white")
        ImageDraw.Draw(image).text((30,30), "Synthetic captured window / 12345", fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        reservation = runtime.store.reserve_asset(mime_type="image/png",scope="activity-content",policy_version=1)
        event = runtime.sensor.emit("content_snapshot",requested_level=CollectionLevel.CONTENT,
            app_id="fixture.exe",payload={"trigger":"typing_pause","scope":"monitor"},
            asset_refs=(reservation.asset_id,), ocr_summary={"status":"ok","text":"Fixture OCR content"})
        runtime.store.commit_asset_bytes(reservation,buffer.getvalue(),event)
        runtime.sensor.emit("input_text",requested_level=CollectionLevel.CONTENT,app_id="fixture.exe",
            payload={"inserted_text":"Fixture committed text","baseline_known":True})
        url = runtime.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            try:
                page = browser.new_page()
                page.on("pageerror",lambda error:errors.append(str(error)))
                for width,height in ((1320,900),(1680,1000),(390,844)):
                    page.set_viewport_size({"width":width,"height":height})
                    page.goto(url)
                    page.wait_for_selector("tr.event")
                    page.wait_for_function("document.getElementById('evidence').textContent.includes('disable_requested')")
                    page.locator("#reveal").check()
                    page.get_by_text("content_snapshot",exact=True).first.click()
                    page.wait_for_function("document.getElementById('frame').naturalWidth > 0")
                    assert page.locator("#frame").is_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(path=str(artifacts/f"monitor-{width}x{height}.png"),full_page=True)
                    page.locator("#reveal").uncheck()
                    page.wait_for_timeout(150)
                    assert not page.locator("#frame").is_visible()
                    assert "Fixture committed text" not in page.locator("#detail").inner_text()
                    page.locator("#pause").click()
                    page.wait_for_function("document.getElementById('collector').textContent.includes('\u5df2\u6682\u505c')")
                    page.locator("#resume").click()
                assert not errors, errors
            finally:
                browser.close()
    finally:
        runtime.close()
    print(json.dumps({"viewports":3,"javascript_errors":errors,"screenshots":str(artifacts)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
