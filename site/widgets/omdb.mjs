const SORTS = ["id", "formula", "anonymous_formula", "names", "spacegroup"];
const API_BASE = "/api";
const OPTIMADE_BASE = "/optimade/v1";

export function searchState(search = "") {
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const id = params.get("compound_cid");
  const material = params.get("material") ?? "";
  const result = params.get("computation") ?? "";
  return {
    id: id && /^\d+$/.test(id) ? id : null,
    structureId: params.get("structure_id"),
    material: material || (id ? "" : "Magnesium"),
    result,
    tab: params.get("tab") || (result ? "tab_computation" : "tab_material"),
    offset: Math.max(0, Number.parseInt(params.get("offset") || "0", 10) || 0),
    sort: SORTS.includes((params.get("sort") || "id").replace(/^-/, "")) ? params.get("sort") || "id" : "id",
  };
}

export function materialsUrl(apiBase, query, offset = 0, sort = "id") {
  const params = new URLSearchParams({ q: query, offset: String(offset), limit: "100", sort });
  return `${apiBase}/materials?${params}`;
}

export function resultsUrl(apiBase, query, offset = 0) {
  const params = new URLSearchParams({ q: query, offset: String(offset), limit: "100" });
  return `${apiBase}/results?${params}`;
}

export function materialErrorMessage(status) {
  return status === 404 ? "Material not found." : "Could not load material details.";
}

export function crysvizUrl(base, payload) {
  const url = new URL(base);
  if (url.protocol !== "https:" && url.protocol !== "http:") throw new TypeError("CrysViz URL must use HTTP or HTTPS");
  url.hash = "";
  url.searchParams.set("widget", "1");
  const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
  return `${url.href}#load-file=${encodeURIComponent("structure.crysviz")}|${encodeURIComponent(encoded)}`;
}

const vec3 = (value) => Array.isArray(value) && value.length === 3 && value.every((n) => typeof n === "number" && Number.isFinite(n));

function inverse3(matrix) {
  if (!Array.isArray(matrix) || matrix.length !== 3 || !matrix.every(vec3)) return null;
  const [[a,b,c],[d,e,f],[g,h,i]] = matrix;
  const det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
  return [
    [(e*i-f*h)/det, (c*h-b*i)/det, (b*f-c*e)/det],
    [(f*g-d*i)/det, (a*i-c*g)/det, (c*d-a*f)/det],
    [(d*h-e*g)/det, (b*g-a*h)/det, (a*e-b*d)/det],
  ];
}

export function crysvizPayload(attributes) {
  const lattice = attributes?.lattice_vectors;
  const cartesian = attributes?.cartesian_site_positions;
  const inverse = inverse3(lattice);
  const labels = attributes?.species_at_sites;
  if (!inverse || !Array.isArray(cartesian) || !cartesian.length || !cartesian.every(vec3) || !Array.isArray(labels) || labels.length !== cartesian.length) return null;
  const species = new Map((Array.isArray(attributes.species) ? attributes.species : []).map((entry) => {
    const symbols = entry?.chemical_symbols;
    const concentrations = entry?.concentration;
    const valid = Array.isArray(symbols) && symbols.length === 1 && typeof symbols[0] === "string" && /^[A-Z][a-z]?$/.test(symbols[0]) &&
      (concentrations == null || (Array.isArray(concentrations) && concentrations.length === 1 && concentrations[0] === 1));
    return [entry?.name, valid ? symbols[0] : null];
  }));
  const elements = labels.map((label) => species.get(label));
  if (!elements.every((symbol) => typeof symbol === "string" && /^[A-Z][a-z]?$/.test(symbol))) return null;
  const positions = cartesian.map((point) => inverse.map((row, column) => point[0] * inverse[0][column] + point[1] * inverse[1][column] + point[2] * inverse[2][column]));
  return { format: "crysviz", version: "2.16", selectedFrameIndex: 0, frames: [{ elements, lattice: lattice.map((row) => [...row]), positions }], display: { spinsActive: false } };
}

function node(tag, text = "", className = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== "") element.textContent = String(text);
  return element;
}

function setStatus(container, message, error = false) {
  container.replaceChildren(node("p", message, `status${error ? " error" : ""}`));
}

function materialTable(data, state, count, offset, sort) {
  const wrapper = node("div", "", "table-wrap");
  const table = node("table");
  const head = node("thead");
  const header = node("tr");
  for (const [key, title] of [["id", "compound_id"], ["formula", "formula"], ["anonymous_formula", "anonymous_formula"], ["names", "Names"], ["spacegroup", "Spacegroup"]]) {
    const th = node("th");
    const button = node("button", title);
    button.type = "button";
    button.dataset.sort = key;
    button.setAttribute("aria-sort", sort.replace(/^-/, "") === key ? (sort.startsWith("-") ? "descending" : "ascending") : "none");
    th.append(button);
    header.append(th);
  }
  head.append(header);
  const body = node("tbody");
  for (const item of data) {
    const row = node("tr");
    const id = String(item.id ?? "");
    const idCell = node("td");
    const link = node("a", id);
    link.href = `?compound_cid=${encodeURIComponent(id)}&material=${encodeURIComponent(state.material)}&sort=${encodeURIComponent(sort)}&offset=${offset}#tab_material`;
    idCell.append(link);
    row.append(idCell);
    for (const value of [item.formula, item.anonymous_formula, Array.isArray(item.names) ? item.names.join(", ") : item.names, item.spacegroup_number]) row.append(node("td", value ?? ""));
    body.append(row);
  }
  table.append(head, body);
  wrapper.append(table);
  wrapper.dataset.count = String(count);
  return wrapper;
}

function resultsTable(data, state = searchState()) {
  const wrapper = node("div", "", "table-wrap");
  const table = node("table");
  const head = node("thead");
  const row = node("tr");
  for (const title of ["Computation", "Project", "Description", "Date", "Code", "Materials", "Minimum energy", "Volume per atom", "Download"]) row.append(node("th", title));
  head.append(row);
  const body = node("tbody");
  for (const item of data) {
    const row = node("tr");
    for (const value of [item.id, item.project, item.description, item.computation_date, item.code]) row.append(node("td", value ?? ""));
    const materials = node("td");
    for (const id of item.compound_ids || []) {
      if (materials.childNodes.length) materials.append(document.createTextNode(", "));
      const link = node("a", id);
      link.href = `?compound_cid=${encodeURIComponent(id)}&computation=${encodeURIComponent(state.result)}&offset=${state.offset}&tab=tab_computation#tab_material`;
      materials.append(link);
    }
    row.append(materials, node("td", item.minimum_energy == null ? "" : `${item.minimum_energy} eV`), node("td", item.volume_per_atom == null ? "" : `${item.volume_per_atom} Å³/atom`));
    const download = node("td");
    if (item.result_structure_id != null && item.result_entry_id != null) {
      for (const [label, file] of [["POSCAR", "POSCAR"], ["CIF", "structure.cif"]]) {
        if (download.childNodes.length) download.append(document.createTextNode(" · "));
        const link = node("a", label);
        link.href = `/api/promoted/structures/${encodeURIComponent(item.result_structure_id)}/${file}`;
        download.append(link);
      }
    } else if (item.result_structure_id != null) download.append(node("span", "Geometry unavailable", "status"));
    row.append(download);
    body.append(row);
  }
  table.append(head, body);
  wrapper.append(table);
  return wrapper;
}

function pager(count, offset, limit, onPage) {
  const bar = node("div", "", "pager");
  const previous = node("button", "Previous");
  previous.type = "button";
  previous.disabled = offset <= 0;
  previous.addEventListener("click", () => onPage(Math.max(0, offset - limit)));
  const next = node("button", "Next");
  next.type = "button";
  next.disabled = offset + limit >= count;
  next.addEventListener("click", () => onPage(offset + limit));
  bar.append(previous, node("span", `${count ? offset + 1 : 0}–${Math.min(count, offset + limit)} of ${count}`), next);
  return bar;
}

function appendField(list, label, value) {
  const item = node("li");
  const strong = node("strong", `${label}: `);
  item.append(strong, document.createTextNode(value == null || value === "" ? "—" : Array.isArray(value) ? value.join(", ") : String(value)));
  list.append(item);
}

function renderDetail(container, material, structures, crysvizBase) {
  container.replaceChildren();
  const title = node("h2", `Data on material: ${material.formula || ""} (${material.id})`);
  const back = node("p");
  const backLink = node("a", "← Back to materials search");
  const backState = searchState(location.search);
  const backParams = backState.result
    ? new URLSearchParams({ computation: backState.result, offset: String(backState.offset), tab: "tab_computation" })
    : new URLSearchParams({ material: backState.material, sort: backState.sort, offset: String(backState.offset) });
  backLink.href = `?${backParams}#tab_material`;
  back.append(backLink);
  container.append(title, back);

  const grid = node("div", "", "detail-grid");
  const main = node("div");
  const general = node("section", "", "detail-section");
  general.append(node("h3", "General Data"));
  const list = node("ul", "", "detail-list");
  appendField(list, "Formula", material.formula);
  appendField(list, "Names", material.names);
  appendField(list, "Anonymous formula", material.anonymous_formula);
  appendField(list, "Element count", material.number_of_elements);
  appendField(list, "Spacegroup number", material.spacegroup_number);
  appendField(list, "Periodicity", material.periodicity);
  general.append(list);
  main.append(general);

  const structureSection = node("section", "", "detail-section");
  structureSection.append(node("h3", "Structure"));
  const selected = material.selected_structure || structures.find((entry) => String(entry.id) === searchState(location.search).structureId) || structures[0];
  const downloads = node("div", "", "link-list");
  if (selected?.id != null && selected?.entry_id != null) {
    for (const [label, file] of [["Download POSCAR", "POSCAR"], ["Download CIF", "structure.cif"]]) {
      const anchor = node("a", label);
      anchor.href = `/api/structures/${encodeURIComponent(selected.id)}/${file}`;
      downloads.append(anchor);
    }
  } else structureSection.append(node("p", "Native structure geometry unavailable; downloads are unavailable.", "status"));
  structureSection.append(downloads);
  const viewerColumn = node("div", "", "viewer-column");
  const viewer = node("div", "", "viewer");
  viewerColumn.append(node("h3", "Structure viewer"), viewer);
  if (selected?.entry_id) {
    viewer.append(node("p", "Loading structure viewer…", "status"));
    fetch(`${OPTIMADE_BASE}/structures/${encodeURIComponent(selected.entry_id)}`)
      .then((response) => { if (!response.ok) throw new Error(`Structure service returned ${response.status}`); return response.json(); })
      .then((payload) => {
        const resource = payload.data || payload;
        const attributes = resource.attributes || resource;
        const session = crysvizPayload(attributes);
        if (!session) throw new Error("Structure data is incomplete for the viewer.");
        const iframe = node("iframe", "", "structure-frame");
        iframe.title = "Interactive crystal structure (CrysViz)";
        iframe.loading = "lazy";
        iframe.referrerPolicy = "no-referrer";
        iframe.setAttribute("sandbox", "allow-scripts allow-popups allow-popups-to-escape-sandbox");
        iframe.src = crysvizUrl(crysvizBase, session);
        viewer.replaceChildren(iframe);
      })
      .catch(() => setStatus(viewer, "Interactive structure viewer unavailable. Use the structure downloads instead.", true));
  } else setStatus(viewer, "Interactive structure viewer unavailable. Use the structure downloads instead.", true);
  if (structures.length) {
    structureSection.append(node("h3", "Specific structure geometries seen for this Compound"));
    const links = node("div", "", "link-list");
    for (const structure of structures) {
      const anchor = node("a", structure.id);
      const params = new URLSearchParams(location.search);
      params.set("compound_cid", material.id);
      params.set("structure_id", structure.id);
      anchor.href = `?${params}#tab_material`;
      links.append(anchor);
    }
    structureSection.append(links);
  }
  main.append(structureSection);

  const related = node("div");
  const computations = node("section", "", "detail-section");
  computations.append(node("h3", "Promoted results"));
  computations.append(resultsTable((material.computations || []).map((item) => ({ ...item, compound_ids: [material.id] })), searchState(location.search)));
  related.append(computations);
  const references = node("section", "", "detail-section");
  references.append(node("h3", "References"));
  const refList = node("ul", "", "detail-list");
  for (const ref of material.references || []) {
    const li = node("li");
    const doi = String(ref.ref || "");
    if (/^10\.\d{4,9}\//.test(doi)) {
      const link = node("a", doi);
      link.href = `https://doi.org/${encodeURIComponent(doi)}`;
      li.append(link);
    } else li.append(document.createTextNode(doi));
    const citation = [ref.authorsstr, ref.journal, ref.volume, ref.firstpage, ref.lastpage, ref.year].filter(Boolean).join("; ");
    if (citation) li.append(document.createTextNode(` — ${citation}`));
    refList.append(li);
  }
  if (!refList.childElementCount) refList.append(node("li", "No references available."));
  references.append(refList);
  related.append(references);
  const tags = node("section", "", "detail-section");
  tags.append(node("h3", "Tags"));
  const tagList = node("div", "", "tag-list");
  for (const tag of material.tags || []) tagList.append(node("span", `${tag.tag}: ${tag.value}`, "tag"));
  if (!tagList.childElementCount) tagList.append(node("span", "No tags available.", "status"));
  tags.append(tagList);
  related.append(tags);
  main.append(related);
  grid.append(main, viewerColumn);
  container.append(grid);
}

async function loadMaterials(container, apiBase, state, onPage) {
  setStatus(container, "Loading materials…");
  try {
    const response = await fetch(materialsUrl(apiBase, state.material, state.offset, state.sort));
    if (!response.ok) throw new Error(`Search service returned ${response.status}`);
    const result = await response.json();
    const data = Array.isArray(result.data) ? result.data : [];
    const table = materialTable(data, state, Number(result.count) || 0, state.offset, state.sort);
    table.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-sort]");
      if (!button) return;
      const key = button.dataset.sort;
      const nextSort = state.sort.replace(/^-/, "") === key && !state.sort.startsWith("-") ? `-${key}` : key;
      const params = new URLSearchParams(location.search);
      params.set("material", state.material);
      params.set("sort", nextSort);
      params.delete("offset");
      location.href = `?${params}#tab_material`;
    });
    container.replaceChildren(node("p", `${result.count ?? data.length} materials found.`), table, pager(Number(result.count) || 0, state.offset, Number(result.limit) || 100, onPage));
  } catch (error) { setStatus(container, `Could not load materials: ${error.message}`, true); }
}

async function loadResults(container, apiBase, state, onPage) {
  setStatus(container, "Loading results…");
  try {
    const response = await fetch(resultsUrl(apiBase, state.result, state.offset));
    if (!response.ok) throw new Error(`Results service returned ${response.status}`);
    const result = await response.json();
    const data = Array.isArray(result.data) ? result.data : [];
    container.replaceChildren(node("p", `${result.count ?? data.length} results found.`), resultsTable(data, state), pager(Number(result.count) || 0, state.offset, Number(result.limit) || 100, onPage));
  } catch (error) { setStatus(container, `Could not load results: ${error.message}`, true); }
}

async function loadDetail(container, apiBase, state, crysvizBase) {
  setStatus(container, "Loading material details…");
  try {
    const params = new URLSearchParams();
    if (state.structureId) params.set("structure_id", state.structureId);
    const suffix = params.size ? `?${params}` : "";
    const response = await fetch(`${apiBase}/materials/${encodeURIComponent(state.id)}${suffix}`);
    if (!response.ok) {
      setStatus(container, materialErrorMessage(response.status), true);
      return;
    }
    const material = await response.json();
    const structures = Array.isArray(material.structures) ? material.structures : [];
    renderDetail(container, material, structures, crysvizBase);
  } catch { setStatus(container, materialErrorMessage(0), true); }
}

function activateTab(tab) {
  const chosen = ["tab_material", "tab_computation", "tab_help", "tab_about"].includes(tab) ? tab : "tab_material";
  document.querySelectorAll("[data-panel]").forEach((panel) => { panel.hidden = panel.dataset.panel !== chosen; });
  document.querySelectorAll("[data-material-detail]").forEach((detail) => { detail.hidden = chosen !== "tab_material"; });
  document.querySelectorAll("[data-tab]").forEach((link) => link.classList.toggle("active", link.dataset.tab === chosen));
}

function boot(root) {
  const config = JSON.parse(document.getElementById(root.dataset.configId)?.textContent || "{}");
  const state = searchState(location.search);
  const materialForm = root.querySelector('[data-search="materials"]');
  const resultForm = root.querySelector('[data-search="results"]');
  materialForm.elements.material.value = state.material;
  resultForm.elements.computation.value = state.result;
  const materialResults = root.querySelector("[data-material-results]");
  const resultResults = root.querySelector("[data-result-results]");
  const detail = root.querySelector("[data-material-detail]");
  const countText = document.querySelector("[data-live-counts]");
  if (countText) fetch(`${API_BASE}/stats`).then((response) => {
    if (!response.ok) throw new Error("Statistics unavailable");
    return response.json();
  }).then((stats) => { countText.textContent = `Present database: Materials: ${stats.compounds ?? "—"} · Computations: ${stats.calculations ?? "—"}`; }).catch(() => { countText.textContent = ""; });
  activateTab(location.hash.slice(1) || state.tab);
  document.querySelectorAll("[data-tab]").forEach((link) => link.addEventListener("click", () => activateTab(link.dataset.tab)));
  materialForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const params = new URLSearchParams({ material: materialForm.elements.material.value.trim() });
    location.href = `?${params}#tab_material`;
  });
  resultForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const params = new URLSearchParams({ computation: resultForm.elements.computation.value.trim(), tab: "tab_computation" });
    location.href = `?${params}#tab_computation`;
  });
  if (state.id) {
    loadDetail(detail, API_BASE, state, config.crysviz_base_url);
  } else if (new URLSearchParams(location.search).has("compound_cid")) {
    setStatus(detail, "Invalid material id.", true);
  }
  else if (new URLSearchParams(location.search).has("material")) loadMaterials(materialResults, API_BASE, state, (offset) => {
    const params = new URLSearchParams(location.search); params.set("offset", String(offset)); location.href = `?${params}#tab_material`;
  });
  if (state.result) loadResults(resultResults, API_BASE, state, (offset) => {
    const params = new URLSearchParams(location.search); params.set("offset", String(offset)); location.href = `?${params}#tab_computation`;
  });
}

if (typeof document !== "undefined") document.querySelectorAll('[data-omdb="1"]').forEach(boot);
