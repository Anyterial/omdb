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
        "crysviz_base_url": os.environ.get("OMDB_CRYSVIZ_BASE_URL", "https://crysviz.org/index.html"),
        "query": dict(context.query),
        "widget_id": context.widget_id,
    }
    asset = WidgetAsset("omdb.mjs", Path(__file__).with_name("omdb.mjs").read_bytes(), "text/javascript")
    html = f'''<section class="omdb" data-omdb="1" data-widget-id="{widget_id}" data-config-id="{config_id}">
  <div class="tab-panel" id="tab_material" data-panel="tab_material">
    <form class="search-box" data-search="materials">
      <h2>Search for All Data Related to a Material</h2>
      <div class="search-row"><input type="text" name="material" aria-label="Material query" /><button type="submit">submit</button></div>
      <p class="hint">Search material formulas, anonymous formulas, and names by text.</p>
    </form>
    <div data-material-results aria-live="polite"></div>
  </div>
  <div class="tab-panel" id="tab_computation" data-panel="tab_computation" hidden>
    <form class="search-box" data-search="results">
      <h2>Search for Results and Projects</h2>
      <div class="search-row"><input type="text" name="computation" aria-label="Result query" placeholder="e.g. formation" /><button type="submit">submit</button></div>
      <p class="hint">Search promoted calculations and projects.</p>
    </form>
    <div data-result-results aria-live="polite"></div>
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
    <div class="about-copy">
      <img class="banner" src="{escape(relative_base, quote=True)}/omdb-largebanner.jpg" alt="Open Materials Database" />
      <h2>About the Open Materials Database</h2>
      <p><strong>The Open Materials Database is a publicly available, searchable database for information on material properties created by computational materials design research.</strong> Its database-centered high-throughput approach builds on ideas pioneered by G. Ceder, K. Persson, and others as part of the Materials Genome Initiative.</p>
      <p>The project was led by Rickard Armiento at Theoretical Physics, Linköping University, with colleagues at Linköping University and the QM-RCD lab at the University of Basel. The database was built with the High-Throughput Toolkit (<em>httk</em>).</p>
      <p>Crystal structures were sourced in large part from the <a href="https://www.crystallography.net/">Crystallography Open Database</a>. The project acknowledged support from the Swedish Research Council and the Linnaeus Environment at Linköping on Nanoscale Functional Materials.</p>
      <p>For publications using this historical database, cite the relevant data source and computational study associated with the material or result.</p>
    </div>
  </div>
  <noscript><p class="status">Search and material details require JavaScript. <a href="{optimade_info}">Browse the OPTIMADE API</a>.</p></noscript>
  <div data-material-detail aria-live="polite"></div>
  <script type="module" src="/_httk/serve/assets/omdb.mjs"></script>
  <script id="{config_id}" type="application/json">{_safe_json(config)}</script>
</section>'''
    return WidgetRenderResult(html, assets=(asset,))
