"""Migrate the legacy OMDB SQLite files into the native httk2 data layout."""

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterable
from contextlib import closing
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from httk.atomistic import Cell, Sites, Species, StructureEntry, UnitcellStructure, UnitcellStructureRecord
from httk.core import content_id
from httk.store import SqliteStore

from .data import CATALOG_LAYOUT, MANIFEST_LAYOUT

FIXED_DENOMINATOR = 1_000_000_000


def _source_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _source_files(source: Path) -> tuple[Path, Path, Path]:
    main = source / "database.sqlite"
    if not main.is_file():
        main = source / "database_50.sqlite"
    promoted = source / "promoted.sqlite"
    contributed = source / "contributed.sqlite"
    for path in (main, promoted, contributed):
        if not path.is_file():
            raise FileNotFoundError(f"Missing legacy OMDB database: {path}")
    return main, promoted, contributed


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    )


def _reference(connection: sqlite3.Connection, reference_id: int) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM Reference WHERE Reference_id=?", (reference_id,)).fetchone()
    if row is None:
        raise ValueError(f"missing Reference {reference_id}")
    authors = [
        {"id": author["Author_id"], "last_name": author["last_name"], "given_names": author["given_names"]}
        for author in connection.execute(
            "SELECT a.Author_id,a.last_name,a.given_names FROM Reference_authors ra "
            "JOIN Author a ON a.Author_id=ra.authors_Author_sid WHERE ra.Reference_sid=? "
            "ORDER BY ra.authors_index,ra.Reference_authors_id",
            (reference_id,),
        )
    ]
    result = {key: row[key] for key in row.keys() if key != "Reference_id"}
    result["id"] = reference_id
    result["authors"] = authors
    return result


def _references(
    connection: sqlite3.Connection, owner_table: str, owner_column: str, owner_id: int
) -> list[dict[str, Any]]:
    if not _has_table(connection, owner_table):
        return []
    rows = connection.execute(
        f"SELECT reference_Reference_sid FROM {owner_table} WHERE {owner_column}=? ORDER BY {owner_table}_id",
        (owner_id,),
    )
    return [_reference(connection, int(row[0])) for row in rows]


def _tags(connection: sqlite3.Connection, table: str, owner_column: str, owner_id: int) -> list[dict[str, Any]]:
    if not _has_table(connection, table):
        return []
    return [
        {"tag": row[0], "value": row[1]}
        for row in connection.execute(
            f"SELECT tag,value FROM {table} WHERE {owner_column}=? ORDER BY {table}_id", (owner_id,)
        )
    ]


def _periodicity(row: sqlite3.Row) -> list[bool]:
    return [bool(row["pbc_0"]), bool(row["pbc_1"]), bool(row["pbc_2"])]


def _species(connection: sqlite3.Connection, structure: sqlite3.Row) -> tuple[tuple[Species, ...], dict[int, str]]:
    symbol_rows = list(
        connection.execute(
            "SELECT symbols_index,symbols FROM Structure_symbols WHERE Structure_sid=? ORDER BY symbols_index",
            (structure["Structure_id"],),
        )
    )
    symbols = {int(row[0]): str(row[1]) for row in symbol_rows}
    if len(symbols) != len(symbol_rows):
        raise ValueError("duplicate Structure_symbols index")
    groups: dict[int, tuple[int, str]] = {}
    group_rows = list(
        connection.execute(
            "SELECT asa.siteassignments_index,sa.SiteAssignment_id,sa.symbol "
            "FROM Assignments_siteassignments asa JOIN SiteAssignment sa "
            "ON sa.SiteAssignment_id=asa.siteassignments_SiteAssignment_sid "
            "WHERE asa.Assignments_sid=? ORDER BY asa.siteassignments_index,asa.Assignments_siteassignments_id",
            (structure["assignments_Assignments_sid"],),
        )
    )
    for row in group_rows:
        groups[int(row[0])] = (int(row[1]), str(row[2]))
    if len(groups) != len(group_rows):
        raise ValueError("duplicate site-assignment group index")
    if set(symbols) != set(groups):
        raise ValueError(
            f"symbol/site-assignment group mismatch: symbols={sorted(symbols)}, assignments={sorted(groups)}"
        )
    species: list[Species] = []
    group_names: dict[int, str] = {}
    used_names: set[str] = set()
    for group in sorted(symbols):
        site_assignment_id, _legacy_name = groups[group]
        components = list(
            connection.execute(
                "SELECT a.symbol,a.ratio,a.weight,a.magnetic_moment_0,a.magnetic_moment_1,a.magnetic_moment_2 "
                "FROM SiteAssignment_assignments saa JOIN Assignment a "
                "ON a.Assignment_id=saa.assignments_Assignment_sid WHERE saa.SiteAssignment_sid=? "
                "ORDER BY saa.assignments_index,saa.SiteAssignment_assignments_id",
                (site_assignment_id,),
            )
        )
        if not components:
            fallback = connection.execute(
                "SELECT symbol,ratio FROM SiteAssignment WHERE SiteAssignment_id=?", (site_assignment_id,)
            ).fetchone()
            if fallback is None:
                raise ValueError(f"missing SiteAssignment {site_assignment_id}")
            components = [fallback]
        if any(any(value is not None for value in item[2:]) for item in components):
            raise ValueError(
                f"SiteAssignment {site_assignment_id} contains constituent mass or magnetic-moment data "
                "that has no lossless native species mapping"
            )
        legacy_name = symbols[group]
        name = legacy_name if legacy_name not in used_names else f"{legacy_name}@{group}"
        used_names.add(name)
        group_names[group] = name
        species.append(
            Species(
                name=name,
                chemical_symbols=[str(item[0]) for item in components],
                concentration=[Fraction(int(item[1]), FIXED_DENOMINATOR) for item in components],
                original_name=legacy_name,
            )
        )
    return tuple(species), group_names


def _structure_record(connection: sqlite3.Connection, structure_id: int, entry_id: str) -> UnitcellStructureRecord:
    structure = connection.execute("SELECT * FROM Structure WHERE Structure_id=?", (structure_id,)).fetchone()
    if structure is None:
        raise ValueError(f"missing Structure {structure_id}")
    cell = connection.execute("SELECT * FROM Cell WHERE Cell_id=?", (structure["uc_cell_Cell_sid"],)).fetchone()
    if cell is None:
        raise ValueError(f"Structure {structure_id} has no unit-cell Cell")
    if structure["uc_sites_UnitcellSites_sid"] is None:
        raise ValueError(f"Structure {structure_id} has no unit-cell sites")
    species, group_names = _species(connection, structure)
    coords: list[tuple[Fraction, Fraction, Fraction]] = []
    species_at_sites: list[str] = []
    sites_id = structure["uc_sites_UnitcellSites_sid"]
    coordinate_rows = list(
        connection.execute(
            "SELECT reduced_coords_index,reduced_coords_0,reduced_coords_1,reduced_coords_2 "
            "FROM UnitcellSites_reduced_coords WHERE UnitcellSites_sid=? "
            "ORDER BY reduced_coords_index,UnitcellSites_reduced_coords_id",
            (sites_id,),
        )
    )
    group_rows = list(
        connection.execute(
            "SELECT coords_groupnumber_index,coords_groupnumber FROM UnitcellSites_coords_groupnumber "
            "WHERE UnitcellSites_sid=? ORDER BY coords_groupnumber_index,UnitcellSites_coords_groupnumber_id",
            (sites_id,),
        )
    )
    coordinate_indexes = [int(row[0]) for row in coordinate_rows]
    group_indexes = [int(row[0]) for row in group_rows]
    if len(set(coordinate_indexes)) != len(coordinate_indexes):
        raise ValueError("duplicate unit-cell coordinate index")
    if len(set(group_indexes)) != len(group_indexes):
        raise ValueError("duplicate unit-cell coordinate-group index")
    if coordinate_indexes != group_indexes:
        raise ValueError(
            f"unit-cell coordinate/group index sets disagree: coordinates={coordinate_indexes}, groups={group_indexes}"
        )
    for coordinate, group_row in zip(coordinate_rows, group_rows, strict=True):
        group = int(group_row[1])
        if group not in group_names:
            raise ValueError(f"coordinate group {group} has no Structure_symbols entry")
        coords.append(tuple(Fraction(int(value), FIXED_DENOMINATOR) for value in coordinate[1:]))
        species_at_sites.append(group_names[group])
    if not coords:
        raise ValueError(f"Structure {structure_id} has no unit-cell coordinates")
    basis = [
        [Fraction(int(cell[f"basis_{row * 3 + column}"]), FIXED_DENOMINATOR) for column in range(3)] for row in range(3)
    ]
    value = UnitcellStructure(
        cell=Cell(basis, periodicity=_periodicity(structure)),
        sites=Sites(coords),
        species=species,
        species_at_sites=species_at_sites,
        chemical_formula_descriptive=structure["formula"],
    )
    return replace(UnitcellStructureRecord.from_structure(value), id=entry_id)


def _structure_metadata(connection: sqlite3.Connection, structure_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT formula,anonymous_formula,spacegroup_number,pbc_0,pbc_1,pbc_2,hall_symbol,hexhash "
        "FROM Structure WHERE Structure_id=?",
        (structure_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"missing Structure {structure_id}")
    return {
        "formula": row["formula"],
        "anonymous_formula": row["anonymous_formula"],
        "spacegroup_number": row["spacegroup_number"],
        "periodicity": _periodicity(row),
        "hall_symbol": row["hall_symbol"],
        "hexhash": row["hexhash"],
        "tags": _tags(connection, "StructureTag", "structure_Structure_sid", structure_id),
        "references": _references(connection, "StructureRef", "structure_Structure_sid", structure_id),
    }


def _catalog_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE materials (
          id INTEGER PRIMARY KEY, formula TEXT, anonymous_formula TEXT, names TEXT NOT NULL,
          spacegroup_number INTEGER, number_of_elements INTEGER, periodicity TEXT NOT NULL,
          tags TEXT NOT NULL, "references" TEXT NOT NULL, structures TEXT NOT NULL,
          default_structure_id INTEGER
        );
        CREATE TABLE geometries (
          id INTEGER PRIMARY KEY, entry_id TEXT, metadata TEXT NOT NULL
        );
        CREATE INDEX geometries_entry_id ON geometries(entry_id);
        CREATE TABLE computations (
          id INTEGER PRIMARY KEY, description TEXT, computation_date TEXT, code TEXT, project TEXT,
          compound_ids TEXT NOT NULL, result_structure_id INTEGER, minimum_energy REAL, volume_per_atom REAL
        );
        CREATE TABLE promoted_geometries (
          id INTEGER PRIMARY KEY, entry_id TEXT, metadata TEXT NOT NULL
        );
        CREATE INDEX promoted_geometries_entry_id ON promoted_geometries(entry_id);
        """
    )
    connection.execute("INSERT INTO metadata VALUES ('layout',?)", (CATALOG_LAYOUT,))


def _selected_main(connection: sqlite3.Connection, limit: int | None) -> tuple[list[sqlite3.Row], list[int]]:
    sql = "SELECT * FROM Compound ORDER BY Compound_id"
    parameters: tuple[int, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        parameters = (limit,)
    compounds = list(connection.execute(sql, parameters))
    compound_ids = [int(row["Compound_id"]) for row in compounds]
    connection.execute("CREATE TEMP TABLE selected_compounds (id INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO selected_compounds VALUES (?)", ((value,) for value in compound_ids))
    linked = [
        int(row[0])
        for row in connection.execute(
            "SELECT DISTINCT cs.structure_Structure_sid FROM CompoundStructure cs "
            "JOIN selected_compounds p ON p.id=cs.compound_Compound_sid ORDER BY cs.structure_Structure_sid"
        )
    ]
    if limit is None:
        structure_ids = [
            int(row[0]) for row in connection.execute("SELECT Structure_id FROM Structure ORDER BY Structure_id")
        ]
    else:
        structure_ids = linked
    return compounds, structure_ids


def _import_geometries(
    connection: sqlite3.Connection,
    catalog: sqlite3.Connection,
    bulk: Any,
    structure_ids: Iterable[int],
    *,
    prefix: str,
    table: str,
    canonical: dict[str, str],
    errors: list[dict[str, Any]],
    started: float,
) -> tuple[dict[int, str | None], int, int]:
    aliases: dict[int, str | None] = {}
    imported = 0
    deduplicated = 0
    for structure_id in structure_ids:
        proposed_id = f"{prefix}{structure_id}"
        try:
            metadata = _structure_metadata(connection, structure_id)
            record = _structure_record(connection, structure_id, proposed_id)
            identity = content_id(record)
        except (KeyError, TypeError, ValueError) as exc:
            entry_id = None
            try:
                metadata = _structure_metadata(connection, structure_id)
            except (KeyError, TypeError, ValueError) as metadata_exc:
                metadata = {"metadata_error": f"{type(metadata_exc).__name__}: {metadata_exc}"}
            message = f"{type(exc).__name__}: {exc}"
            errors.append({"source": table, "structure_id": structure_id, "error": message})
        else:
            entry_id = canonical.get(identity)
            if entry_id is None:
                bulk.save(record)
                canonical[identity] = proposed_id
                entry_id = proposed_id
                imported += 1
            else:
                deduplicated += 1
        aliases[structure_id] = entry_id
        catalog.execute(
            f"INSERT INTO {table}(id,entry_id,metadata) VALUES (?,?,?)",
            (structure_id, entry_id, _json(metadata)),
        )
        processed = len(aliases)
        if processed % 1_000 == 0:
            print(
                f"{table}: processed {processed} geometries "
                f"({len(errors)} total errors, {time.monotonic() - started:.1f}s elapsed)",
                file=sys.stderr,
                flush=True,
            )
    return aliases, imported, deduplicated


def _insert_materials(
    source: sqlite3.Connection,
    catalog: sqlite3.Connection,
    compounds: Iterable[sqlite3.Row],
    aliases: dict[int, str | None],
) -> None:
    for compound in compounds:
        compound_id = int(compound["Compound_id"])
        links = list(
            source.execute(
                "SELECT cs.structure_Structure_sid,EXISTS(SELECT 1 FROM StructureTag st "
                "WHERE st.structure_Structure_sid=cs.structure_Structure_sid AND st.tag='source') source_tagged "
                "FROM CompoundStructure cs WHERE cs.compound_Compound_sid=? "
                "ORDER BY source_tagged DESC,cs.structure_Structure_sid,cs.CompoundStructure_id",
                (compound_id,),
            )
        )
        structures = [{"id": int(row[0]), "entry_id": aliases.get(int(row[0]))} for row in links]
        default_id = int(links[0][0]) if links else None
        names = [
            row[0]
            for row in source.execute(
                "SELECT name FROM CompoundName WHERE compound_Compound_sid=? ORDER BY CompoundName_id", (compound_id,)
            )
        ]
        catalog.execute(
            "INSERT INTO materials VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                compound_id,
                compound["formula"],
                compound["anonymous_formula"],
                _json(names),
                compound["spacegroup_number"],
                compound["number_of_elements"],
                _json(_periodicity(compound)),
                _json(_tags(source, "CompoundTag", "compound_Compound_sid", compound_id)),
                _json(_references(source, "CompoundRef", "compound_Compound_sid", compound_id)),
                _json(structures),
                default_id,
            ),
        )


def _promoted_selection(
    promoted: sqlite3.Connection, main_by_hash: dict[str, list[int]], *, restricted: bool
) -> tuple[list[sqlite3.Row], list[int]]:
    promoted.execute("CREATE TEMP TABLE selected_main_hashes (hexhash TEXT PRIMARY KEY)")
    promoted.executemany("INSERT INTO selected_main_hashes VALUES (?)", ((value,) for value in main_by_hash))
    restriction = (
        "WHERE EXISTS (SELECT 1 FROM ComputationRelatedCompound crc JOIN Compound pc "
        "ON pc.Compound_id=crc.compound_Compound_sid JOIN selected_main_hashes mh ON mh.hexhash=pc.hexhash "
        "WHERE crc.computation_Computation_sid=c.Computation_id) "
        if restricted
        else ""
    )
    rows = list(
        promoted.execute(
            "SELECT c.*,r.relaxed_structure_Structure_sid,r.minimum_energy,r.volume_per_atom,"
            "code.name code_name,code.version code_version,p.name project_name "
            "FROM Computation c LEFT JOIN Result_RelaxedCellResult r ON r.computation_Computation_sid=c.Computation_id "
            "LEFT JOIN Code code ON code.Code_id=c.code_Code_sid "
            "LEFT JOIN ComputationProject cp ON cp.computation_Computation_sid=c.Computation_id "
            "LEFT JOIN Project p ON p.Project_id=cp.project_Project_sid "
            f"{restriction}ORDER BY c.Computation_id"
        )
    )
    structure_ids = sorted(
        {int(row["relaxed_structure_Structure_sid"]) for row in rows if row["relaxed_structure_Structure_sid"]}
    )
    return rows, structure_ids


def _insert_computations(
    promoted: sqlite3.Connection,
    catalog: sqlite3.Connection,
    rows: Iterable[sqlite3.Row],
    main_by_hash: dict[str, list[int]],
) -> int:
    unmatched = 0
    for row in rows:
        compound_ids = sorted(
            {
                compound_id
                for item in promoted.execute(
                    "SELECT pc.hexhash FROM ComputationRelatedCompound crc JOIN Compound pc "
                    "ON pc.Compound_id=crc.compound_Compound_sid WHERE crc.computation_Computation_sid=?",
                    (row["Computation_id"],),
                )
                for compound_id in main_by_hash.get(item[0], ())
            }
        )
        if not compound_ids:
            unmatched += 1
        code = row["code_name"]
        if row["code_version"]:
            code = f"{code} {row['code_version']}" if code else row["code_version"]
        catalog.execute(
            "INSERT INTO computations VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row["Computation_id"],
                row["description"],
                row["computation_date"],
                code,
                row["project_name"],
                _json(compound_ids),
                row["relaxed_structure_Structure_sid"],
                row["minimum_energy"],
                row["volume_per_atom"],
            ),
        )
    return unmatched


def migrate(source: Path, output: Path, *, limit: int | None = None, workers: int = 1) -> dict[str, Any]:
    """Migrate a legacy OMDB directory into a new atomic output directory.

    :param source: Directory containing the three legacy SQLite databases.
    :param output: New destination directory; it must not exist.
    :param limit: Restrict the migration to the first N main compounds.
    :param workers: Number of native-store bulk encoder processes.
    :return: The manifest written to the output directory.
    :raises FileExistsError: If the output path already exists.
    :raises FileNotFoundError: If a required legacy source database is missing.
    :raises ValueError: If ``limit`` or ``workers`` is not positive.
    """
    source = Path(source).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive integer")
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    main_path, promoted_path, _contributed_path = _source_files(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    started = time.monotonic()
    errors: list[dict[str, Any]] = []
    try:
        with closing(_source_connection(main_path)) as main, closing(_source_connection(promoted_path)) as promoted:
            compounds, main_structure_ids = _selected_main(main, limit)
            main_by_hash: dict[str, list[int]] = {}
            for row in compounds:
                main_by_hash.setdefault(str(row["hexhash"]), []).append(int(row["Compound_id"]))
            promoted_rows, promoted_structure_ids = _promoted_selection(
                promoted, main_by_hash, restricted=limit is not None
            )
            catalog = sqlite3.connect(staging / "catalog.sqlite")
            try:
                _catalog_schema(catalog)
                canonical: dict[str, str] = {}
                with SqliteStore(
                    staging / "structures.sqlite", entry_records={StructureEntry: UnitcellStructureRecord}
                ) as store:
                    with store.bulk_ingest(
                        chunk_size=256,
                        finalize="deferred",
                        track_sids=False,
                        verify_metadata=False,
                        workers=workers,
                    ) as bulk:
                        main_aliases, main_imported, main_deduplicated = _import_geometries(
                            main,
                            catalog,
                            bulk,
                            main_structure_ids,
                            prefix="omdb.structures-1-",
                            table="geometries",
                            canonical=canonical,
                            errors=errors,
                            started=started,
                        )
                        _promoted_aliases, promoted_imported, promoted_deduplicated = _import_geometries(
                            promoted,
                            catalog,
                            bulk,
                            promoted_structure_ids,
                            prefix="omdb.promoted.structures-1-",
                            table="promoted_geometries",
                            canonical=canonical,
                            errors=errors,
                            started=started,
                        )
                _insert_materials(main, catalog, compounds, main_aliases)
                unmatched = _insert_computations(promoted, catalog, promoted_rows, main_by_hash)
                catalog.commit()
            finally:
                catalog.close()
        manifest = {
            "layout": MANIFEST_LAYOUT,
            "catalog_layout": CATALOG_LAYOUT,
            "source": str(source),
            "source_files": {
                path.name: {
                    "path": str(path.resolve()),
                    "size": path.stat().st_size,
                    "mtime_ns": path.stat().st_mtime_ns,
                }
                for path in (main_path, promoted_path, _contributed_path)
            },
            "limit": limit,
            "workers": workers,
            "counts": {
                "materials": len(compounds),
                "main_geometries": len(main_structure_ids),
                "main_geometries_imported": main_imported,
                "main_geometry_aliases": main_deduplicated,
                "promoted_geometries": len(promoted_structure_ids),
                "promoted_geometries_imported": promoted_imported,
                "promoted_geometry_aliases": promoted_deduplicated,
                "computations": len(promoted_rows),
                "unmatched_computations": unmatched,
                "geometry_errors": len(errors),
            },
            "geometry_errors": errors,
            "elapsed_seconds": time.monotonic() - started,
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        # Builders targeting the same output must still be operator-coordinated: a
        # directory created in the tiny interval between this check and rename can
        # be replaced on platforms that permit replacing an empty directory.
        if output.exists():
            raise FileExistsError(f"Output already exists: {output}")
        staging.rename(output)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main(argv: list[str] | None = None) -> int:
    """Run the command-line migration.

    :param argv: Command arguments, or ``None`` for process arguments.
    :return: Exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="legacy www-omdb-data directory")
    parser.add_argument("--output", required=True, type=Path, help="new output directory")
    parser.add_argument("--limit", type=int, help="migrate only the first N main compounds")
    parser.add_argument("--workers", type=int, default=1, help="native-store encoder processes (default: 1)")
    args = parser.parse_args(argv)
    try:
        manifest = migrate(args.source, args.output, limit=args.limit, workers=args.workers)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
