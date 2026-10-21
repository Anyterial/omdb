"""Check the composed services against a small synthetic material catalog."""

import json
import sqlite3
from dataclasses import replace

import pytest
from httk.atomistic import Cell, Sites, StructureEntry, UnitcellStructure
from httk.atomistic.storage.records import UnitcellStructureRecord
from httk.store import Backend, SqlStore

from omdb.data import CATALOG_LAYOUT, MANIFEST_LAYOUT


@pytest.fixture
def dataset(tmp_path):
    """Build two synthetic crystals and a catalog with names and a promoted result."""
    database = Backend.sqlite(tmp_path / "structures.sqlite")
    store = SqlStore(database, entry_records={StructureEntry: UnitcellStructureRecord})
    for identifier, symbol in ((1, "Mg"), (2, "O")):
        structure = UnitcellStructure(
            Cell([[3, 0, 0], [0, 3, 0], [0, 0, 3]]), Sites([[0, 0, 0]]), species_at_sites=[symbol]
        )
        store.save(replace(UnitcellStructureRecord.from_structure(structure), id=f"omdb.structures-1-{identifier}"))
    relaxed = UnitcellStructure(Cell([[5, 0, 0], [0, 5, 0], [0, 0, 5]]), Sites([[0, 0, 0]]), species_at_sites=["Mg"])
    store.save(replace(UnitcellStructureRecord.from_structure(relaxed), id="omdb.promoted.structures-1-1"))
    database.dispose()
    connection = sqlite3.connect(tmp_path / "catalog.sqlite")
    connection.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE materials (id INTEGER PRIMARY KEY, formula TEXT, anonymous_formula TEXT, names TEXT,
          spacegroup_number INTEGER, number_of_elements INTEGER, periodicity TEXT, tags TEXT, "references" TEXT,
          structures TEXT, default_structure_id INTEGER);
        CREATE TABLE geometries (id INTEGER PRIMARY KEY, entry_id TEXT, metadata TEXT NOT NULL);
        CREATE TABLE promoted_geometries (id INTEGER PRIMARY KEY, entry_id TEXT, metadata TEXT NOT NULL);
        CREATE TABLE computations (id INTEGER PRIMARY KEY, description TEXT, computation_date TEXT,
          code TEXT, project TEXT, compound_ids TEXT, result_structure_id INTEGER,
          minimum_energy REAL, volume_per_atom REAL);
    """)
    connection.execute("INSERT INTO metadata VALUES ('layout',?)", (CATALOG_LAYOUT,))
    for identifier, formula, name in ((1, "Mg", "Magnesium"), (2, "O", "Oxygen <script>alert(1)</script>")):
        connection.execute(
            "INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                formula,
                "A",
                json.dumps([name] if identifier == 1 else [name, 'Synthetic "Quoted" name']),
                221,
                1,
                "[1,1,1]",
                '[{"tag":"scope","value":"compound"}]',
                "[]",
                json.dumps([{"id": identifier, "entry_id": f"omdb.structures-1-{identifier}"}]),
                identifier,
            ),
        )
        connection.execute(
            "INSERT INTO geometries VALUES (?,?,?)",
            (
                identifier,
                f"omdb.structures-1-{identifier}",
                json.dumps({"tags": [{"tag": "scope", "value": f"geometry-{identifier}"}]}),
            ),
        )
    connection.execute("INSERT INTO promoted_geometries VALUES (1,'omdb.promoted.structures-1-1','{}')")
    connection.execute(
        "INSERT INTO computations VALUES (1,?,?,?,?,?,?,?,?)",
        (
            "Relaxation",
            "2014-07-29",
            "VASP",
            "formation-energies",
            "[1]",
            1,
            -1.25,
            27.0,
        ),
    )
    connection.commit()
    connection.close()
    (tmp_path / "manifest.json").write_text(json.dumps({"layout": MANIFEST_LAYOUT, "catalog_layout": CATALOG_LAYOUT}))
    return tmp_path
