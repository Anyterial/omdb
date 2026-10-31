# Open Materials Database

A renewal of the original OMDB website using *httk₂*. The compact Materials,
Results, Help and About interface retains the original compound IDs, names,
references, tags and geometry links. CrysViz replaces JSmol. A standard OPTIMADE
structures endpoint runs in the same application.

Python 3.12+ is required. Run these commands from this checkout. In the shared
workspace, the Makefile uses `../../.venv/bin/python`; elsewhere, install the
application and its dependencies in a virtual environment first:

```sh
python -m pip install -e '.[dev]'
make build_database
make serve
```

Open <http://127.0.0.1:8080/>. Try **Magnesium** in Materials search: the search
matches the original Names, formula and anonymous formula fields. Column buttons
sort the complete matching set; Previous/Next retrieve pages of 100 compounds.
Search is a literal, case-insensitive SQLite substring search, so old IQL,
quoted/wildcard expressions and element-prefix expressions are not interpreted.

The Results tab searches the promoted calculations by description, project and
code. Total energies are in eV and volumes per atom in Å³, as in the source.
Standard material geometries and promoted relaxed geometries have separate
CIF/POSCAR downloads; a standard geometry is not presented as a relaxed result.
The historical contributed database contains no calculations; submission,
accounts, structure uploads and original calculation archive downloads are not
implemented.

The importer reads `../omdb-old/www-omdb-data/database.sqlite`, `promoted.sqlite`
and `contributed.sqlite`. Override the source and output directory as needed:

```sh
make build_database SOURCE=/path/to/www-omdb-data DATABASE=data/omdb-new
make serve DATABASE=data/omdb-new HOST=127.0.0.1 PORT=8080
```

The source files are opened read-only. A build creates a new directory containing
`catalog.sqlite`, `structures.sqlite` and `manifest.json`, using a temporary
sibling directory until the build finishes. An existing destination is refused.
To rebuild, choose a new destination, inspect its manifest and test it before
switching the server to it. Keep the old output until the replacement is accepted.
Do not run builds against changing source databases.
Use distinct destinations for concurrent builds and keep the destination path
unmodified until its build finishes.

The full historical dataset is large. `BUILD_ARGS='--workers 2'` uses two native
encoding workers and reduced sample build time by about one third on this
workstation. Serial rebuilding can take hours; progress is printed every 1,000
geometries. Worker count does not change canonical ID assignment.

For a small development build:

```sh
make build_database DATABASE=data/sample BUILD_ARGS='--limit 100'
make serve DATABASE=data/sample
```

A limited build contains only the first N compounds, their geometries and related
promoted calculations. Its header reports its own counts. It is not the complete
database. Generated databases are ignored by Git and are not included in wheels.
Copy the completed output directory to the serving host; the application never
silently falls back to sample data or the legacy database.

The manifest records imported counts, native-content aliases and geometry errors.
Check `counts.geometry_errors` and `geometry_errors` before accepting a build.
Every legacy geometry retains a catalog entry, including entries whose native
conversion failed. Their structure availability is explicit. Exact basis and
fractional-coordinate integers are retained as fractions over 10⁹; the importer
does not standardize cells, recompute symmetry, or round stored coordinates.
Native structure content duplicates share a canonical entry ID; all legacy links
remain in the catalog. Main and promoted geometry IDs occupy separate namespaces.

The website catalog and native structures are served read-only. Environment
variable `OMDB_DATA_DIR` selects a built directory when no CLI `--database` is
supplied. `OMDB_CRYSVIZ_BASE_URL` overrides the default CrysViz iframe host at
`https://crysviz.org/`, for example for a separately deployed CrysViz
installation. The viewer uses the structure's actual lattice and sites. Structures
with mixed or partially occupied sites are passed to CrysViz as the CIF download,
which carries the occupancies; they are not replaced with an invented ordered
geometry.

OPTIMADE discovery and examples:

- `/optimade/versions`
- `/optimade/v1/info`
- `/optimade/v1/info/structures`
- `/optimade/v1/structures?filter=elements%20HAS%20%22Mg%22&page_limit=10`

OPTIMADE provides native structures; the application catalog at `/api/materials`
and `/api/results` supplies the historical compound names and calculation tables.
Legacy `index.php?material=Magnesium`, `index.php?compound_cid=100`,
`index.php?compound_cid=100&structure_id=...` and `index.php?vasp_sid=...` links
continue to work. Other PHP/IQL routes are not reproduced.

For deployment, use the ASGI factory `omdb.app:create_app` with an ASGI server,
for example `uvicorn omdb.app:create_app --factory --host 127.0.0.1 --port 8080`,
and set `OMDB_DATA_DIR` to an absolute directory. A reverse proxy can provide TLS;
mount the combined application at the origin root. OPTIMADE constructs its public
links from the request, so either configure trusted proxy headers in the ASGI server
or call `create_app(data_dir, public_base_url="https://omdb.example.org/")` from a
small ASGI module to fix them. Add
`optimade_base_url="https://optimade.omdb.example.org/"` to serve OPTIMADE at the
root of its own host as well: requests with that `Host` header reach OPTIMADE, and
its links name that host. The reverse proxy must forward the original `Host` header
without rewriting the path. The website keeps using its same-origin `/optimade`
mount. No hosted service is published by this repository.

Verification:

```sh
make ci
python -m build
python -m pip install -e '.[browser]'
python -m playwright install chromium
make test-browser
HTTK_TEST_OMDB_SOURCE=/path/to/www-omdb-data make test-real-data
```

`OMDB_BROWSER_EXECUTABLE` optionally selects an existing Chromium executable.
`OMDB_BROWSER=firefox` selects Firefox instead (install it with Playwright first).
The browser gate runs a real local ASGI server and checks a synthetic dataset;
it intercepts the external CrysViz host and verifies the generated session payload.
Set `HTTK_TEST_OMDB_CRYSVIZ_LIVE=1` to also check the remote widget renders the
loaded Mg structure using WebGL. CI retains this live gate; it requires network
access and working browser graphics.

The legacy dataset and source archives remain outside this repository. The
original OMDB branding is retained in the website assets. No new dataset
redistribution permission is asserted here.
