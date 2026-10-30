"""Compose the OMDB website, catalog and native OPTIMADE service."""

import json
import os
import tempfile
from contextlib import asynccontextmanager, closing
from pathlib import Path
from urllib.parse import urlsplit

from httk.atomistic import StructureEntry, UnitcellStructureView
from httk.core import save
from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.optimade import OptimadeConfig, adapter_from_stores
from httk.serve.optimade import create_asgi_app as create_optimade_app
from httk.serve.web import create_asgi_app as create_web_app
from httk.store.backend.sql import StoredEntrySource
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .data import CATALOG_LAYOUT, MANIFEST_LAYOUT, entry_record, open_catalog, open_structures

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(__file__).parent / "site" if (Path(__file__).parent / "site").is_dir() else ROOT / "site"
DEFAULT_DATA = ROOT / "data" / "omdb"
_JSON_FIELDS = {"names", "periodicity", "tags", "references", "structures", "compound_ids"}
_SORTS = {
    "id": "id",
    "formula": "formula",
    "anonymous_formula": "anonymous_formula",
    "names": "names",
    "spacegroup": "spacegroup_number",
}


class _LegacyEntry:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] == "/index.php":
            request = Request(scope)
            if "vasp_sid" in request.query_params:
                try:
                    geometry_id = _integer(request.query_params["vasp_sid"], "structure id", minimum=1)
                except HTTPException:
                    response = Response("Invalid structure id.", status_code=400)
                else:
                    response = RedirectResponse(f"/api/structures/{geometry_id}/POSCAR", status_code=307)
                await response(scope, receive, send)
                return
            scope = dict(scope, path="/", raw_path=b"/")
        await self.app(scope, receive, send)


class _OptimadeHost:
    """Serve the ``/optimade`` mount at the root of a dedicated host."""

    def __init__(self, app, host):
        self.app = app
        self.host = host

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"host", b"").decode("latin-1").lower() == self.host:
            path = "/optimade" + scope["path"]
            scope = dict(scope, path=path, raw_path=path.encode())
        await self.app(scope, receive, send)


def _origin(url):
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.path not in {"", "/"}
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
    ):
        raise ValueError("optimade_base_url must be an HTTP(S) origin without a path")
    return f"{parts.scheme}://{parts.netloc.lower()}"


def _decoded(row):
    return {key: json.loads(value) if key in _JSON_FIELDS else value for key, value in dict(row).items()}


def _integer(value, name, *, minimum=0, maximum=2**63 - 1):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"Invalid {name}.") from None
    if result < minimum or (maximum is not None and result > maximum):
        raise HTTPException(400, f"Invalid {name}.")
    return result


def _page(request):
    return (
        _integer(request.query_params.get("offset", "0"), "offset"),
        _integer(request.query_params.get("limit", "100"), "limit", minimum=1, maximum=100),
    )


def _pattern(query):
    if len(query) > 1000:
        raise HTTPException(400, "Search text is too long.")
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def create_app(
    data_dir: str | Path | None = None,
    *,
    site_dir: str | Path | None = None,
    public_base_url: str | None = None,
    optimade_base_url: str | None = None,
) -> Starlette:
    """Open a built OMDB dataset and compose its read-only services.

    :param data_dir: Directory produced by the offline importer.
    :param site_dir: Site source directory, defaulting to this checkout's site.
    :param public_base_url: Public origin the application is served at, e.g.
        ``https://omdb.example.org/``; OPTIMADE links then use it instead of
        deriving them from each request.
    :param optimade_base_url: Separate public origin for OPTIMADE, e.g.
        ``https://optimade.omdb.example.org/``. Requests whose ``Host`` header
        names it reach OPTIMADE at the root, and its links use it. The website
        keeps using its own ``/optimade`` mount.
    :return: Application owning its opened database and web engine.
    :raises FileNotFoundError: If a required built database is missing.
    :raises ValueError: If ``optimade_base_url`` is not an HTTP(S) origin.
    """
    if optimade_base_url is not None:
        optimade_origin = _origin(optimade_base_url)
    elif public_base_url is not None:
        optimade_origin = public_base_url.rstrip("/") + "/optimade"
    else:
        optimade_origin = None
    directory = Path(data_dir or os.environ.get("OMDB_DATA_DIR", DEFAULT_DATA)).resolve()
    for filename in ("catalog.sqlite", "structures.sqlite", "manifest.json"):
        if not (directory / filename).is_file():
            raise FileNotFoundError(f"Missing {directory / filename}; run make build_database first")
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("layout") != MANIFEST_LAYOUT or manifest.get("catalog_layout") != CATALOG_LAYOUT:
        raise ValueError("Unsupported OMDB build manifest; run make build_database with a new destination")
    catalog_path = directory / "catalog.sqlite"
    with closing(open_catalog(catalog_path)):
        pass
    database, store = open_structures(directory / "structures.sqlite")
    web = None
    try:
        adapter = adapter_from_stores((StoredEntrySource(store, StructureEntry, "omdb-structures"),))
        optimade = create_optimade_app(
            adapter,
            OptimadeConfig(
                provider={
                    "name": "Open Materials Database",
                    "description": "OMDB crystal structures",
                    "prefix": "omdb",
                },
                page_limit_max=100,
            ),
            baseurl=optimade_origin,
        )
        web = create_web_app(site_dir or SITE)
        web.add_middleware(_LegacyEntry)

        def stats(request: Request):
            with closing(open_catalog(catalog_path)) as connection:
                counts = {
                    key: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for key, table in (
                        ("compounds", "materials"),
                        ("geometries", "geometries"),
                        ("calculations", "computations"),
                    )
                }
            return JSONResponse(counts)

        def materials(request: Request):
            offset, limit = _page(request)
            query = request.query_params.get("q", "")
            pattern = _pattern(query)
            sort = request.query_params.get("sort", "id")
            column = _SORTS.get(sort.removeprefix("-"))
            if column is None:
                raise HTTPException(400, "Invalid sort column.")
            direction = "DESC" if sort.startswith("-") else "ASC"
            where = (
                "WHERE formula LIKE ? ESCAPE '\\' OR anonymous_formula LIKE ? ESCAPE '\\' OR EXISTS "
                "(SELECT 1 FROM json_each(materials.names) WHERE value LIKE ? ESCAPE '\\')"
            )
            parameters = (pattern, pattern, pattern)
            with closing(open_catalog(catalog_path)) as connection:
                count = connection.execute(f"SELECT COUNT(*) FROM materials {where}", parameters).fetchone()[0]
                rows = connection.execute(
                    "SELECT id,formula,anonymous_formula,names,spacegroup_number,number_of_elements "
                    f"FROM materials {where} ORDER BY {column} {direction}, id ASC LIMIT ? OFFSET ?",
                    (*parameters, limit, offset),
                ).fetchall()
            return JSONResponse(
                {"data": [_decoded(row) for row in rows], "count": count, "offset": offset, "limit": limit}
            )

        def material(request: Request):
            material_id = _integer(request.path_params["id"], "material id", minimum=1)
            with closing(open_catalog(catalog_path)) as connection:
                row = connection.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone()
                if row is None:
                    raise HTTPException(404, "Material not found.")
                result = _decoded(row)
                # JSON membership matches whole numeric ids, never a substring of another id.
                computations = connection.execute(
                    "SELECT c.*,g.entry_id AS result_entry_id FROM computations c "
                    "LEFT JOIN promoted_geometries g ON g.id=c.result_structure_id WHERE EXISTS "
                    "(SELECT 1 FROM json_each(c.compound_ids) WHERE value=?) ORDER BY c.id",
                    (material_id,),
                ).fetchall()
            result["computations"] = [_decoded(item) for item in computations]
            selected_id = _integer(
                request.query_params.get("structure_id", result["default_structure_id"]), "structure id", minimum=1
            )
            selected = next((item for item in result["structures"] if item["id"] == selected_id), None)
            if selected is None:
                raise HTTPException(404, "Geometry does not belong to this material.")
            with closing(open_catalog(catalog_path)) as connection:
                geometry = connection.execute("SELECT metadata FROM geometries WHERE id=?", (selected_id,)).fetchone()
            selected = {**selected, "metadata": json.loads(geometry["metadata"])}
            if "structure_id" in request.query_params:
                result["tags"] = selected["metadata"].get("tags", [])
            result["selected_structure"] = selected
            return JSONResponse(result)

        def results(request: Request):
            offset, limit = _page(request)
            pattern = _pattern(request.query_params.get("q", ""))
            where = "WHERE description LIKE ? ESCAPE '\\' OR project LIKE ? ESCAPE '\\' OR code LIKE ? ESCAPE '\\'"
            parameters = (pattern, pattern, pattern)
            with closing(open_catalog(catalog_path)) as connection:
                count = connection.execute(f"SELECT COUNT(*) FROM computations {where}", parameters).fetchone()[0]
                rows = connection.execute(
                    "SELECT c.*,g.entry_id AS result_entry_id FROM computations c "
                    f"LEFT JOIN promoted_geometries g ON g.id=c.result_structure_id {where} "
                    "ORDER BY c.id LIMIT ? OFFSET ?",
                    (*parameters, limit, offset),
                ).fetchall()
            return JSONResponse(
                {"data": [_decoded(row) for row in rows], "count": count, "offset": offset, "limit": limit}
            )

        def download(request: Request):
            geometry_id = _integer(request.path_params["id"], "structure id", minimum=1)
            filename = request.path_params["filename"]
            formats = {"POSCAR": ("vasp-poscar", "text/plain"), "structure.cif": ("cif", "chemical/x-cif")}
            if filename not in formats:
                raise HTTPException(404, "Download not found.")
            table = "promoted_geometries" if request.url.path.startswith("/api/promoted/") else "geometries"
            with closing(open_catalog(catalog_path)) as connection:
                row = connection.execute(f"SELECT entry_id FROM {table} WHERE id=?", (geometry_id,)).fetchone()
            if row is None or row["entry_id"] is None:
                raise HTTPException(404, "Structure not available.")
            record = entry_record(store, row["entry_id"])
            if record is None:
                raise HTTPException(404, "Structure not available.")
            file_format, media_type = formats[filename]
            with tempfile.TemporaryDirectory(prefix="omdb-download-") as temporary:
                path = Path(temporary) / filename
                try:
                    save(UnitcellStructureView(record), path, format=file_format)
                except (ValueError, TypeError) as exc:
                    raise HTTPException(422, "This structure cannot be represented in the requested format.") from exc
                body = path.read_bytes()
            return Response(
                body,
                media_type=media_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Content-Type-Options": "nosniff",
                },
            )

        @asynccontextmanager
        async def catalog_lifespan(_app):
            try:
                yield
            finally:
                database.dispose()

        api = Starlette(
            lifespan=catalog_lifespan,
            routes=[
                Route("/stats", stats),
                Route("/materials", materials),
                Route("/materials/{id}", material),
                Route("/results", results),
                Route("/structures/{id}/{filename}", download),
                Route("/promoted/structures/{id}/{filename}", download),
            ],
        )
        # Route the legacy entry point directly through the site app, preserving its query string.
        app = compose_asgi_apps(
            [ASGIAppMount("/api", api), ASGIAppMount("/optimade", optimade)], root=ASGIAppMount("/", web)
        )
        if optimade_base_url is not None:
            app.add_middleware(_OptimadeHost, host=urlsplit(optimade_origin).netloc)
        app.state.database = database
        app.state.store = store
        return app
    except BaseException:
        if web is not None:
            web.state.engine.close()
        database.dispose()
        raise
