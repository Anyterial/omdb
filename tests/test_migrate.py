"""Focused tests for the loss-explicit legacy OMDB migration."""

import json
import sqlite3
from contextlib import closing
from fractions import Fraction
from pathlib import Path

import pytest
from httk.atomistic import UnitcellStructureView

from omdb.data import entry_record, open_catalog, open_structures
from omdb.migrate import migrate


def _legacy_schema(connection: sqlite3.Connection, *, promoted: bool = False) -> None:
    connection.executescript(
        """
        CREATE TABLE Compound (Compound_id INTEGER PRIMARY KEY, formula TEXT, anonymous_formula TEXT,
          spacegroup_number INTEGER, number_of_elements INTEGER, pbc_0 INTEGER, pbc_1 INTEGER, pbc_2 INTEGER,
          hexhash TEXT);
        CREATE TABLE CompoundStructure (CompoundStructure_id INTEGER PRIMARY KEY,
          compound_Compound_sid INTEGER, structure_Structure_sid INTEGER);
        CREATE TABLE CompoundName (CompoundName_id INTEGER PRIMARY KEY, compound_Compound_sid INTEGER, name TEXT);
        CREATE TABLE CompoundTag (CompoundTag_id INTEGER PRIMARY KEY, compound_Compound_sid INTEGER, tag TEXT, value TEXT);
        CREATE TABLE CompoundRef (CompoundRef_id INTEGER PRIMARY KEY, compound_Compound_sid INTEGER,
          reference_Reference_sid INTEGER);
        CREATE TABLE Reference (Reference_id INTEGER PRIMARY KEY, ref TEXT, authorsstr TEXT, journal TEXT, volume TEXT,
          firstpage TEXT, lastpage TEXT, year TEXT, publisher TEXT, publisher_extra TEXT, hexhash TEXT);
        CREATE TABLE Author (Author_id INTEGER PRIMARY KEY, last_name TEXT, given_names TEXT, hexhash TEXT);
        CREATE TABLE Reference_authors (Reference_authors_id INTEGER PRIMARY KEY, Reference_sid INTEGER,
          authors_index INTEGER, authors_Author_sid INTEGER);
        CREATE TABLE Structure (Structure_id INTEGER PRIMARY KEY, assignments_Assignments_sid INTEGER,
          uc_sites_UnitcellSites_sid INTEGER, uc_cell_Cell_sid INTEGER, formula TEXT, anonymous_formula TEXT,
          spacegroup_number INTEGER, pbc_0 INTEGER, pbc_1 INTEGER, pbc_2 INTEGER, hall_symbol TEXT, hexhash TEXT);
        CREATE TABLE Cell (Cell_id INTEGER PRIMARY KEY, basis_0 INTEGER, basis_1 INTEGER, basis_2 INTEGER,
          basis_3 INTEGER, basis_4 INTEGER, basis_5 INTEGER, basis_6 INTEGER, basis_7 INTEGER, basis_8 INTEGER);
        CREATE TABLE Structure_symbols (Structure_symbols_id INTEGER PRIMARY KEY, Structure_sid INTEGER,
          symbols_index INTEGER, symbols TEXT);
        CREATE TABLE Assignments_siteassignments (Assignments_siteassignments_id INTEGER PRIMARY KEY,
          Assignments_sid INTEGER, siteassignments_index INTEGER, siteassignments_SiteAssignment_sid INTEGER);
        CREATE TABLE SiteAssignment (SiteAssignment_id INTEGER PRIMARY KEY, atomic_number TEXT, ratio INTEGER,
          symbol TEXT);
        CREATE TABLE SiteAssignment_assignments (SiteAssignment_assignments_id INTEGER PRIMARY KEY,
          SiteAssignment_sid INTEGER, assignments_index INTEGER, assignments_Assignment_sid INTEGER);
        CREATE TABLE Assignment (Assignment_id INTEGER PRIMARY KEY, atomic_number INTEGER, weight INTEGER,
          ratio INTEGER, magnetic_moment_0 INTEGER, magnetic_moment_1 INTEGER, magnetic_moment_2 INTEGER,
          symbol TEXT);
        CREATE TABLE UnitcellSites_reduced_coords (UnitcellSites_reduced_coords_id INTEGER PRIMARY KEY,
          UnitcellSites_sid INTEGER, reduced_coords_index INTEGER, reduced_coords_0 INTEGER,
          reduced_coords_1 INTEGER, reduced_coords_2 INTEGER);
        CREATE TABLE UnitcellSites_coords_groupnumber (UnitcellSites_coords_groupnumber_id INTEGER PRIMARY KEY,
          UnitcellSites_sid INTEGER, coords_groupnumber_index INTEGER, coords_groupnumber INTEGER);
        CREATE TABLE StructureTag (StructureTag_id INTEGER PRIMARY KEY, structure_Structure_sid INTEGER,
          tag TEXT, value TEXT);
        CREATE TABLE StructureRef (StructureRef_id INTEGER PRIMARY KEY, structure_Structure_sid INTEGER,
          reference_Reference_sid INTEGER);
        """
    )
    if promoted:
        connection.executescript(
            """
            CREATE TABLE Computation (Computation_id INTEGER PRIMARY KEY, computation_date TEXT,
              description TEXT, code_Code_sid INTEGER);
            CREATE TABLE Code (Code_id INTEGER PRIMARY KEY, name TEXT, version TEXT);
            CREATE TABLE Project (Project_id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE ComputationProject (ComputationProject_id INTEGER PRIMARY KEY,
              computation_Computation_sid INTEGER, project_Project_sid INTEGER);
            CREATE TABLE ComputationRelatedCompound (ComputationRelatedCompound_id INTEGER PRIMARY KEY,
              computation_Computation_sid INTEGER, compound_Compound_sid INTEGER);
            CREATE TABLE Result_RelaxedCellResult (Result_RelaxedCellResult_id INTEGER PRIMARY KEY,
              computation_Computation_sid INTEGER, relaxed_structure_Structure_sid INTEGER,
              minimum_energy REAL, volume_per_atom REAL);
            """
        )


def _structure(
    connection: sqlite3.Connection,
    sid: int,
    *,
    x: int = 125_000_000,
    malformed: bool = False,
    mixed: bool = False,
) -> None:
    connection.execute(
        "INSERT INTO Structure VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (sid, sid, sid, sid, "FeNi" if mixed else "Fe", "AB" if mixed else "A", 1, 1, 1, 1, "P 1", f"s{sid}"),
    )
    connection.execute(
        "INSERT INTO Cell VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, 2_500_000_001, 0, 0, 0, 3_000_000_000, 0, 0, 0, 4_000_000_000),
    )
    connection.execute("INSERT INTO Structure_symbols VALUES (?,?,?,?)", (sid, sid, 0, "FeNi" if mixed else "Fe"))
    connection.execute("INSERT INTO Assignments_siteassignments VALUES (?,?,?,?)", (sid, sid, 0, sid))
    connection.execute(
        "INSERT INTO SiteAssignment VALUES (?,?,?,?)",
        (sid, 0 if mixed else 26, 1_000_000_000, "FeNi" if mixed else "Fe"),
    )
    components = (("Fe", 750_000_000), ("Ni", 250_000_000)) if mixed else (("Fe", 1_000_000_000),)
    for index, (symbol, ratio) in enumerate(components):
        assignment_id = sid * 10 + index
        connection.execute(
            "INSERT INTO Assignment VALUES (?,?,?,?,?,?,?,?)",
            (assignment_id, 26 if symbol == "Fe" else 28, None, ratio, None, None, None, symbol),
        )
        connection.execute(
            "INSERT INTO SiteAssignment_assignments VALUES (?,?,?,?)",
            (assignment_id, sid, index, assignment_id),
        )
    connection.execute(
        "INSERT INTO UnitcellSites_reduced_coords VALUES (?,?,?,?,?,?)", (sid, sid, 0, x, 250_000_000, 500_000_000)
    )
    if not malformed:
        connection.execute("INSERT INTO UnitcellSites_coords_groupnumber VALUES (?,?,?,?)", (sid, sid, 0, 0))


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    source = tmp_path / "legacy"
    source.mkdir()
    with closing(sqlite3.connect(source / "database.sqlite")) as main:
        _legacy_schema(main)
        main.executemany(
            "INSERT INTO Compound VALUES (?,?,?,?,?,?,?,?,?)",
            [(1, "FeNi", "AB", 1, 2, 1, 1, 1, "match"), (2, "Fe", "A", 1, 1, 1, 1, 1, "second")],
        )
        main.executemany("INSERT INTO CompoundStructure VALUES (?,?,?)", [(1, 1, 10), (2, 1, 11), (3, 2, 12)])
        main.execute("INSERT INTO CompoundName VALUES (1,1,'Synthetic alloy')")
        main.execute("INSERT INTO CompoundTag VALUES (1,1,'kind','fixture')")
        main.execute(
            "INSERT INTO Reference VALUES (1,'ref text','Doe, J.','Journal','1','2','3','2026','Pub','x','r1')"
        )
        main.execute("INSERT INTO Author VALUES (1,'Doe','Jane','a1')")
        main.execute("INSERT INTO Reference_authors VALUES (1,1,0,1)")
        main.execute("INSERT INTO CompoundRef VALUES (1,1,1)")
        _structure(main, 10, mixed=True)
        _structure(main, 11, mixed=True)
        _structure(main, 12, malformed=True)
        _structure(main, 13, x=333_333_333)  # deliberately unlinked; full imports retain it
        main.execute("INSERT INTO StructureTag VALUES (1,11,'source','preferred')")
        main.execute("INSERT INTO StructureRef VALUES (1,10,1)")
        main.commit()
    with closing(sqlite3.connect(source / "promoted.sqlite")) as promoted:
        _legacy_schema(promoted, promoted=True)
        promoted.executemany(
            "INSERT INTO Compound VALUES (?,?,?,?,?,?,?,?,?)",
            [(99, "FeNi", "AB", 1, 2, 1, 1, 1, "match"), (100, "Co", "A", 1, 1, 1, 1, 1, "unmatched")],
        )
        _structure(promoted, 50, x=400_000_000)
        _structure(promoted, 51, x=600_000_000)
        promoted.execute("INSERT INTO Code VALUES (1,'code','2.0')")
        promoted.execute("INSERT INTO Project VALUES (1,'project')")
        for cid, compound, structure in ((7, 99, 50), (8, 100, 51)):
            promoted.execute("INSERT INTO Computation VALUES (?,?,?,1)", (cid, "2026-01-02", f"calculation {cid}"))
            promoted.execute("INSERT INTO ComputationProject VALUES (?,?,1)", (cid, cid))
            promoted.execute("INSERT INTO ComputationRelatedCompound VALUES (?,?,?)", (cid, cid, compound))
            promoted.execute(
                "INSERT INTO Result_RelaxedCellResult VALUES (?,?,?,?,?)",
                (cid, cid, structure, -float(cid), 10.0 + cid),
            )
        promoted.commit()
    sqlite3.connect(source / "contributed.sqlite").close()
    return source


def test_migrate_exact_data_aliases_metadata_errors_and_reopen(legacy: Path, tmp_path: Path) -> None:
    output = tmp_path / "built"
    manifest = migrate(legacy, output, limit=2)
    assert manifest["counts"] == {
        "materials": 2,
        "main_geometries": 3,
        "main_geometries_imported": 1,
        "main_geometry_aliases": 1,
        "promoted_geometries": 1,
        "promoted_geometries_imported": 1,
        "promoted_geometry_aliases": 0,
        "computations": 1,
        "unmatched_computations": 0,
        "geometry_errors": 1,
    }
    assert manifest["geometry_errors"][0]["structure_id"] == 12
    with closing(open_catalog(output / "catalog.sqlite")) as catalog:
        material = catalog.execute("SELECT * FROM materials WHERE id=1").fetchone()
        assert json.loads(material["names"]) == ["Synthetic alloy"]
        assert json.loads(material["references"])[0]["authors"][0]["last_name"] == "Doe"
        assert material["default_structure_id"] == 11
        structures = json.loads(material["structures"])
        assert {item["entry_id"] for item in structures} == {"omdb.structures-1-10"}
        assert catalog.execute("SELECT entry_id FROM geometries WHERE id=12").fetchone()[0] is None
        computation = catalog.execute("SELECT * FROM computations").fetchone()
        assert json.loads(computation["compound_ids"]) == [1]
        assert computation["code"] == "code 2.0"
        with pytest.raises(sqlite3.OperationalError):
            catalog.execute("INSERT INTO metadata VALUES ('x','y')")
    database, store = open_structures(output / "structures.sqlite")
    try:
        record = entry_record(store, "omdb.structures-1-10")
        assert record is not None
        structure = UnitcellStructureView(record)
        assert structure.cell.basis.to_fractions_approx()[0][0] == Fraction(2_500_000_001, 1_000_000_000)
        assert structure.sites.reduced_coords.to_fractions()[0][0] == Fraction(1, 8)
        assert record.species[0].constituents[0].concentration == Fraction(3, 4)
        assert record.species[0].constituents[1].concentration == Fraction(1, 4)
        assert entry_record(store, "omdb.structures-1-11") is None
    finally:
        database.dispose()


def test_full_keeps_unlinked_geometry_and_unmatched_computation(legacy: Path, tmp_path: Path) -> None:
    output = tmp_path / "full"
    manifest = migrate(legacy, output)
    assert manifest["counts"]["main_geometries"] == 4
    assert manifest["counts"]["computations"] == 2
    assert manifest["counts"]["unmatched_computations"] == 1
    with closing(open_catalog(output / "catalog.sqlite")) as catalog:
        assert catalog.execute("SELECT entry_id FROM geometries WHERE id=13").fetchone()[0] == "omdb.structures-1-13"
        assert json.loads(catalog.execute("SELECT compound_ids FROM computations WHERE id=8").fetchone()[0]) == []


def test_existing_output_is_never_replaced(legacy: Path, tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep"
    marker.write_text("untouched", encoding="utf-8")
    with pytest.raises(FileExistsError):
        migrate(legacy, output, limit=1)
    assert marker.read_text(encoding="utf-8") == "untouched"


def test_output_created_during_migration_is_preserved(
    legacy: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import omdb.migrate as migration_module

    output = tmp_path / "late-existing"
    original = migration_module._insert_computations

    def create_foreign_output(*args, **kwargs):
        result = original(*args, **kwargs)
        output.mkdir()
        output.joinpath("foreign").write_text("untouched", encoding="utf-8")
        return result

    monkeypatch.setattr(migration_module, "_insert_computations", create_foreign_output)
    with pytest.raises(FileExistsError):
        migration_module.migrate(legacy, output, limit=1)
    assert output.joinpath("foreign").read_text(encoding="utf-8") == "untouched"
    assert list(tmp_path.glob(f".{output.name}.tmp-*")) == []


def test_parallel_bulk_keeps_public_identity_and_aliases_deterministic(legacy: Path, tmp_path: Path) -> None:
    outputs = (tmp_path / "parallel-a", tmp_path / "parallel-b")
    manifests = [migrate(legacy, output, limit=2, workers=2) for output in outputs]
    assert manifests[0]["counts"] == manifests[1]["counts"]
    assert outputs[0].joinpath("catalog.sqlite").read_bytes() == outputs[1].joinpath("catalog.sqlite").read_bytes()

    root_rows = []
    for output in outputs:
        with closing(sqlite3.connect(output / "structures.sqlite")) as connection:
            root_rows.append(
                connection.execute(
                    "SELECT id,immutable_id,content_id,logical_id FROM atomistic_unitcell_structure ORDER BY id"
                ).fetchall()
            )
        with closing(open_catalog(output / "catalog.sqlite")) as catalog:
            assert catalog.execute("SELECT entry_id FROM geometries WHERE id=11").fetchone()[0] == (
                "omdb.structures-1-10"
            )
    assert root_rows[0] == root_rows[1]


def test_coordinate_index_validation_rejects_mismatch_and_duplicates(legacy: Path, tmp_path: Path) -> None:
    with closing(sqlite3.connect(legacy / "database.sqlite")) as main:
        # Structure 12 already has coordinate index 0 but no group. Giving it one
        # group at index 1 makes the row counts equal while the index sets differ.
        main.execute("INSERT INTO UnitcellSites_coords_groupnumber VALUES (1200,12,1,0)")
        _structure(main, 14)
        main.execute("INSERT INTO UnitcellSites_reduced_coords VALUES (1400,14,0,125000000,250000000,500000000)")
        _structure(main, 15)
        main.execute("INSERT INTO UnitcellSites_coords_groupnumber VALUES (1500,15,0,0)")
        main.commit()

    manifest = migrate(legacy, tmp_path / "index-errors")
    errors = {item["structure_id"]: item["error"] for item in manifest["geometry_errors"]}
    assert "index sets disagree" in errors[12]
    assert "duplicate unit-cell coordinate index" in errors[14]
    assert "duplicate unit-cell coordinate-group index" in errors[15]
