import assert from "node:assert/strict";
import test from "node:test";

import { crysvizPayload, crysvizUrl, materialErrorMessage, materialsUrl, resultsUrl, searchState } from "../site/widgets/omdb.mjs";

test("legacy search URLs retain the material, sort, and page", () => {
  assert.deepEqual(searchState("?material=Magnesium&sort=-formula&offset=100"), {
    id: null,
    structureId: null,
    material: "Magnesium",
    result: "",
    tab: "tab_material",
    offset: 100,
    sort: "-formula",
  });
  assert.equal(searchState("?material=NaCl&sort=invalid").sort, "id");
  assert.equal(searchState("?compound_cid=42&structure_id=7&material=NaCl").id, "42");
  assert.equal(searchState("?compound_cid=bad").id, null);
});

test("search requests use the browser API's bounded paging contract", () => {
  assert.equal(materialsUrl("/api", "Na Cl", 100, "-formula"), "/api/materials?q=Na+Cl&offset=100&limit=100&sort=-formula");
  assert.equal(resultsUrl("/api", "Formation Energy", 0), "/api/results?q=Formation+Energy&offset=0&limit=100");
});

test("material detail distinguishes missing records from service failures", () => {
  assert.equal(materialErrorMessage(404), "Material not found.");
  assert.equal(materialErrorMessage(503), "Could not load material details.");
  assert.equal(materialErrorMessage(0), "Could not load material details.");
});

test("CrysViz receives fractional positions derived from Cartesian coordinates", () => {
  const payload = crysvizPayload({
    lattice_vectors: [[2, 0, 0], [0, 4, 0], [0, 0, 5]],
    cartesian_site_positions: [[1, 2, 2.5]],
    species_at_sites: ["Fe-site"],
    species: [{ name: "Fe-site", chemical_symbols: ["Fe"], concentration: [1] }],
  });
  assert.deepEqual(payload.frames[0].positions, [[0.5, 0.5, 0.5]]);
  assert.deepEqual(payload.frames[0].elements, ["Fe"]);
  assert.match(crysvizUrl("https://crysviz.org/", payload), /^https:\/\/crysviz\.org\/\?widget=1#load-file=/);
  const configuredUrl = crysvizUrl("https://crysviz.org/#old-secret", payload);
  assert.match(configuredUrl, /^https:\/\/crysviz\.org\/\?widget=1#load-file=/);
  assert.equal(configuredUrl.includes("old-secret"), false);
  assert.throws(() => crysvizUrl("javascript:alert(1)", payload), /HTTP or HTTPS/);
});

test("CrysViz rejects disordered, partially occupied, and vacancy species", () => {
  const base = {
    lattice_vectors: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
    cartesian_site_positions: [[0, 0, 0]],
    species_at_sites: ["site"],
  };
  for (const species of [
    [{ name: "site", chemical_symbols: ["Fe", "Mn"], concentration: [0.5, 0.5] }],
    [{ name: "site", chemical_symbols: ["Fe"], concentration: [0.75] }],
    [{ name: "site", chemical_symbols: ["vacancy"], concentration: [1] }],
  ]) assert.equal(crysvizPayload({ ...base, species }), null);
});
