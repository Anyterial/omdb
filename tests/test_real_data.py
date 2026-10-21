"""Opt-in checks against an operator-supplied historical OMDB snapshot."""

import json
import os
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path

import pytest
from httk.atomistic import UnitcellStructureView
from starlette.testclient import TestClient

from omdb.app import create_app
from omdb.data import entry_record, open_catalog, open_structures
from omdb.migrate import migrate

pytestmark = pytest.mark.real_data


def test_source_migration_and_serving(tmp_path):
    """Compare source numbers and names with a real limited migration and service."""
    configured = os.environ.get("HTTK_TEST_OMDB_SOURCE")
    if not configured:
        pytest.skip("set HTTK_TEST_OMDB_SOURCE to a legacy www-omdb-data directory")
    source = Path(configured).resolve()
    output = tmp_path / "real"
    manifest = migrate(source, output, limit=1000, workers=2)
    assert manifest["counts"]["materials"] == 1000
    assert manifest["counts"]["geometry_errors"] == 0
    with closing(sqlite3.connect(f"{(source / 'database.sqlite').resolve().as_uri()}?mode=ro", uri=True)) as old:
        sid, cell_id, sites_id = old.execute(
            "SELECT Structure_id,uc_cell_Cell_sid,uc_sites_UnitcellSites_sid FROM Structure ORDER BY Structure_id LIMIT 1"
        ).fetchone()
        basis = old.execute(
            "SELECT " + ",".join(f"basis_{i}" for i in range(9)) + " FROM Cell WHERE Cell_id=?", (cell_id,)
        ).fetchone()
        coords = old.execute(
            "SELECT reduced_coords_0,reduced_coords_1,reduced_coords_2 FROM UnitcellSites_reduced_coords "
            "WHERE UnitcellSites_sid=? ORDER BY reduced_coords_index",
            (sites_id,),
        ).fetchall()
        expected_names = [
            row[0]
            for row in old.execute(
                "SELECT DISTINCT c.Compound_id FROM Compound c LEFT JOIN CompoundName n ON n.compound_Compound_sid=c.Compound_id "
                "WHERE c.Compound_id IN (SELECT Compound_id FROM Compound ORDER BY Compound_id LIMIT 1000) "
                "AND (n.name LIKE '%Magnesium%' OR c.formula LIKE '%Magnesium%' OR c.anonymous_formula LIKE '%Magnesium%') "
                "ORDER BY c.Compound_id"
            )
        ]
    with closing(open_catalog(output / "catalog.sqlite")) as catalog:
        alias = catalog.execute("SELECT entry_id FROM geometries WHERE id=?", (sid,)).fetchone()[0]
    database, store = open_structures(output / "structures.sqlite")
    try:
        structure = UnitcellStructureView(entry_record(store, alias))
        assert structure.cell.basis.to_fractions_approx() == [
            [Fraction(basis[i * 3 + j], 10**9) for j in range(3)] for i in range(3)
        ]
        assert structure.sites.reduced_coords.to_fractions() == [
            [Fraction(value, 10**9) for value in row] for row in coords
        ]
    finally:
        database.dispose()
    with TestClient(create_app(output)) as client:
        matches = client.get("/api/materials?q=Magnesium").json()
        assert matches["count"] == len(expected_names) > 0
        assert [item["id"] for item in matches["data"]] == expected_names
        assert client.get(f"/optimade/v1/structures/{alias}").status_code == 200
        assert client.get(f"/api/structures/{sid}/structure.cif").status_code == 200
    assert json.loads((output / "manifest.json").read_text())["source_files"]
