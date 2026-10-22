"""Render the legacy-style Open Materials Database browser interface."""

import json
import os
from html import escape
from pathlib import Path

from httk.serve.web.widgets import WidgetAsset, WidgetRenderResult


def _safe_json(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render(context, **props):
    """Return the interface shell and its browser asset."""
    del props
    relative_base = context.page.get("relbaseurl", ".")
    if not isinstance(relative_base, str) or not relative_base or relative_base.startswith("/"):
        relative_base = "."
    widget_id = escape(context.widget_id, quote=True)
    config_id = escape(f"site-omdb-{context.widget_id}-config", quote=True)
    optimade_info = "/optimade/v1/info"
    config = {
        "crysviz_base_url": os.environ.get("OMDB_CRYSVIZ_BASE_URL", "https://crysviz.org/"),
        "query": dict(context.query),
        "widget_id": context.widget_id,
    }
    about = (Path(__file__).parents[1] / "templates" / "about.html").read_text(encoding="utf-8")
    about = about.replace("{{ relbaseurl }}", escape(relative_base, quote=True))
    asset = WidgetAsset("omdb.mjs", Path(__file__).with_name("omdb.mjs").read_bytes(), "text/javascript")
    html = f'''<section class="omdb" data-omdb="1" data-widget-id="{widget_id}" data-config-id="{config_id}">
  <div class="search-strip tab-panel" id="tab_material" data-panel="tab_material">
    <form class="search-box" data-search="materials">
      <h3>Search for All Data Related to a Material</h3>
      <input type="text" name="material" aria-label="Material query" />
      <button type="submit">submit</button>
      <details class="search-help"><summary>Search help</summary><p>Type part of a formula, anonymous formula, or name. Search uses a simple text match.</p></details>
    </form>
    <div class="search-results" data-material-results aria-live="polite"></div>
  </div>
  <div class="search-strip tab-panel" id="tab_computation" data-panel="tab_computation" hidden>
    <form class="search-box" data-search="results">
      <h3>Search for Results and Projects</h3>
      <input type="text" name="computation" aria-label="Result query" placeholder="e.g. formation" />
      <button type="submit">submit</button>
      <details class="search-help"><summary>Search help</summary><p>Search result and project descriptions with a simple text match.</p></details>
    </form>
    <div class="search-results" data-result-results aria-live="polite"></div>
  </div>
  <div class="tab-panel" id="tab_help" data-panel="tab_help" hidden>
    <div class="about-copy">
      <h2>Help</h2>
      <h3>Materials Search</h3>
      <p>Search material formulas, anonymous formulas, and names with a simple text query. Select a material to view its geometry, related promoted results, references, and tags.</p>
      <h3>Results Search</h3>
      <p>Search promoted calculations and projects by their descriptions and names.</p>
      <p><a href="{optimade_info}">OPTIMADE API information</a></p>
    </div>
  </div>
  <div class="tab-panel" id="tab_about" data-panel="tab_about" hidden>
    {about}
  </div>
  <noscript><p class="status">Search and material details require JavaScript. <a href="{optimade_info}">Browse the OPTIMADE API</a>.</p></noscript>
  <div data-material-detail aria-live="polite"></div>
  <script type="module" src="/_httk/serve/assets/omdb.mjs"></script>
  <script id="{config_id}" type="application/json">{_safe_json(config)}</script>
</section>'''
    return WidgetRenderResult(html, assets=(asset,))
