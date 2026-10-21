"""Read-only access to a migrated OMDB dataset."""

import sqlite3
from pathlib import Path

import sqlalchemy
from httk.atomistic import StructureEntry, UnitcellStructureRecord
from httk.store import Backend, SqlStore

CATALOG_LAYOUT = "omdb-catalog-v1"
MANIFEST_LAYOUT = "omdb-migration-manifest-v1"


def open_catalog(path: Path) -> sqlite3.Connection:
    """Open and validate an OMDB catalog in SQLite read-only mode.

    :param path: Path to ``catalog.sqlite``.
    :return: A read-only connection whose rows support named access.
    :raises FileNotFoundError: If the catalog does not exist.
    :raises ValueError: If the catalog layout marker is absent or unsupported.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        try:
            row = connection.execute("SELECT value FROM metadata WHERE key='layout'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"Invalid OMDB catalog layout in {path}") from exc
        if row is None or row[0] != CATALOG_LAYOUT:
            actual = None if row is None else row[0]
            raise ValueError(f"Unsupported OMDB catalog layout {actual!r}; expected {CATALOG_LAYOUT!r}")
    except BaseException:
        connection.close()
        raise
    return connection


def open_structures(path: Path) -> tuple[Backend, SqlStore]:
    """Open an OMDB native structure store without write access.

    :param path: Path to ``structures.sqlite``.
    :return: The owning SQL backend and its structure store.
    :raises FileNotFoundError: If the store does not exist.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    url = f"sqlite:///file:{path.as_posix()}?mode=ro&uri=true"
    database = Backend(sqlalchemy.create_engine(url))
    try:
        store = SqlStore(database, entry_records={StructureEntry: UnitcellStructureRecord})
    except BaseException:
        database.dispose()
        raise
    return database, store


def entry_record(store: SqlStore, entry_id: str) -> UnitcellStructureRecord | None:
    """Fetch one native structure by its public entry id.

    :param store: An open structure store.
    :param entry_id: Public structure id recorded in the catalog.
    :return: The matching native record, or ``None``.
    """
    searcher = store.searcher()
    variable = searcher.variable(UnitcellStructureRecord)
    searcher.add(variable.id == entry_id)
    searcher.set_limit(1)
    for row in searcher.results(record=variable):
        return row.record
    return None
