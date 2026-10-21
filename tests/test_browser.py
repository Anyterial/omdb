"""Exercise the real website and catalog together in Chromium."""

import base64
import json
import os
import socket
import sqlite3
import threading
import time
from urllib.parse import unquote

import pytest
import uvicorn

from omdb.app import create_app

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("live_crysviz", [False, True], ids=["interface", "live-crysviz"])
def test_materials_results_and_crysviz(dataset, tmp_path, live_crysviz):
    """Check live navigation, text-safe rendering and the generated CrysViz payload."""
    playwright = pytest.importorskip("playwright.sync_api")
    if live_crysviz and os.environ.get("HTTK_TEST_OMDB_CRYSVIZ_LIVE") != "1":
        pytest.skip("set HTTK_TEST_OMDB_CRYSVIZ_LIVE=1 for remote CrysViz and WebGL acceptance")
    with sqlite3.connect(dataset / "catalog.sqlite") as connection:
        for identifier in range(3, 105):
            connection.execute(
                "INSERT INTO materials SELECT ?,formula,anonymous_formula,names,spacegroup_number,number_of_elements,"
                'periodicity,tags,"references",structures,default_structure_id FROM materials WHERE id=1',
                (identifier,),
            )
    app = create_app(dataset)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("OMDB server did not start")
            time.sleep(0.05)
        with playwright.sync_playwright() as runtime:
            executable = os.environ.get("OMDB_BROWSER_EXECUTABLE")
            browser_type = os.environ.get("OMDB_BROWSER", "chromium")
            launcher = getattr(runtime, browser_type)
            browser = launcher.launch(
                executable_path=executable,
                args=["--no-sandbox", "--enable-unsafe-swiftshader"] if browser_type == "chromium" else [],
            )
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Remote CrysViz is checked separately; this deterministic browser gate
                # inspects the real session passed to it without depending on another host.
                if not live_crysviz:
                    page.route("https://crysviz.org/**", lambda route: route.fulfill(body="<html>CrysViz host</html>"))
                origin = f"http://127.0.0.1:{port}"
                page.goto(origin + "/index.php?material=Magnesium")
                page.get_by_role("link", name="1", exact=True).wait_for()
                assert page.locator("[data-material-results] tbody tr").count() == 100
                assert "Magnesium" in page.locator("[data-material-results]").inner_text()
                page.get_by_role("button", name="Next", exact=True).click()
                page.wait_for_url("**offset=100**")
                page.locator("[data-material-results] tbody tr").first.wait_for()
                assert page.locator("[data-material-results] tbody tr").count() == 3
                page.get_by_role("button", name="Previous", exact=True).click()
                page.get_by_role("link", name="1", exact=True).wait_for()
                page.locator('[data-sort="formula"]').click()
                page.get_by_role("link", name="1", exact=True).wait_for()
                assert "sort=formula" in page.url
                page.get_by_role("link", name="1", exact=True).click()
                frame = page.get_by_title("Interactive crystal structure (CrysViz)")
                frame.wait_for()
                src = frame.get_attribute("src")
                session = json.loads(base64.b64decode(unquote(src.split("|", 1)[1])))
                assert session["frames"][0]["elements"] == ["Mg"]
                assert session["frames"][0]["positions"] == [[0, 0, 0]]
                assert session["frames"][0]["lattice"] == [[3, 0, 0], [0, 3, 0], [0, 0, 3]]
                if live_crysviz:
                    viewer = frame.element_handle().content_frame()
                    viewer.locator("canvas").first.wait_for(timeout=30000)
                    # Inspect the dependency's existing state inside its own frame;
                    # no test-only hooks are added to either application.
                    viewer.wait_for_function(
                        """async () => {
                      const {fileBrowser} = await import(new URL('./state/store.js', location.href).href);
                      const structure = fileBrowser.selectedStructure;
                      return structure?.atoms?.length === 1 && structure.atoms[0].element === 'Mg';
                    }""",
                        timeout=30000,
                    )
                assert (
                    page.get_by_role("link", name="Download POSCAR", exact=True).get_attribute("href")
                    == "/api/structures/1/POSCAR"
                )
                page.screenshot(path=str(tmp_path / "omdb-detail.png"), full_page=True)
                page.get_by_role("link", name="Help", exact=True).click()
                assert page.locator("#tab_help").is_visible()
                assert not page.locator("[data-material-detail]").is_visible()
                page.goto(origin + "/?computation=formation&tab=tab_computation#tab_computation")
                page.locator("[data-result-results] tbody tr").wait_for()
                assert "VASP" in page.locator("[data-result-results]").inner_text()
                page.goto(origin + "/?material=Oxygen")
                page.locator("[data-material-results] tbody tr").wait_for()
                assert page.locator("[data-material-results] script").count() == 0
                assert "<script>" in page.locator("[data-material-results]").inner_text()
                page.goto(origin + "/?compound_cid=999")
                page.get_by_text("Material not found.", exact=True).wait_for()
                assert errors == []
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
