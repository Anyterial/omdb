"""Check the composed services against a small synthetic material catalog."""

import pytest
from starlette.testclient import TestClient

from omdb.app import create_app


@pytest.fixture
def client(dataset):
    """Enter the actual composed application lifecycle."""
    with TestClient(create_app(dataset)) as result:
        yield result


def test_names_search_pagination_and_sort(client):
    response = client.get("/api/materials", params={"q": "Magnesium"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["data"][0]["names"] == ["Magnesium"]
    first = client.get("/api/materials", params={"limit": 1, "sort": "-id"}).json()
    second = client.get("/api/materials", params={"limit": 1, "sort": "-id", "offset": 1}).json()
    assert first["count"] == second["count"] == 2
    assert [first["data"][0]["id"], second["data"][0]["id"]] == [2, 1]


def test_search_literals_and_invalid_inputs(client):
    for query in ("%", "_", "[", "\\", "' OR 1=1 --"):
        assert client.get("/api/materials", params={"q": query}).json()["count"] == 0
    quoted = client.get("/api/materials", params={"q": '"Quoted"'}).json()
    assert quoted["count"] == 1
    assert quoted["data"][0]["id"] == 2
    for params in ({"offset": -1}, {"limit": 101}, {"limit": 0}, {"sort": "id;DROP TABLE materials"}):
        assert client.get("/api/materials", params=params).status_code == 400
    assert client.get("/api/materials/1", params={"structure_id": 2}).status_code == 404
    assert client.get("/api/materials/999").status_code == 404
    assert client.get("/api/materials/no").status_code == 400


def test_detail_and_promoted_results(client):
    detail = client.get("/api/materials/1").json()
    assert detail["selected_structure"]["id"] == 1
    assert detail["selected_structure"]["entry_id"] == "omdb.structures-1-1"
    assert detail["tags"] == [{"tag": "scope", "value": "compound"}]
    geometry = client.get("/api/materials/1?structure_id=1").json()
    assert geometry["tags"] == [{"tag": "scope", "value": "geometry-1"}]
    assert detail["computations"][0]["minimum_energy"] == -1.25
    assert detail["computations"][0]["result_entry_id"] == "omdb.promoted.structures-1-1"
    assert client.get("/api/materials/2").json()["computations"] == []
    result = client.get("/api/results", params={"q": "formation"}).json()
    assert result["count"] == 1
    assert result["data"][0]["compound_ids"] == [1]
    assert client.get("/api/stats").json() == {"compounds": 2, "geometries": 2, "calculations": 1}


def test_optimade_discovery_filter_and_detail(client):
    assert client.get("/optimade/versions").status_code == 200
    assert client.get("/optimade/v1/info").status_code == 200
    assert client.get("/optimade/v1/info/structures").status_code == 200
    response = client.get("/optimade/v1/structures", params={"filter": 'elements HAS "Mg"', "page_limit": 1})
    assert response.status_code == 200, response.text
    envelope = response.json()
    assert envelope["meta"]["data_returned"] == 2
    assert len(envelope["data"]) == 1
    next_page = client.get(envelope["links"]["next"]).json()
    assert {envelope["data"][0]["id"], next_page["data"][0]["id"]} == {
        "omdb.structures-1-1",
        "omdb.promoted.structures-1-1",
    }
    detail = client.get("/optimade/v1/structures/omdb.structures-1-1").json()["data"]
    assert detail["attributes"]["cartesian_site_positions"] == [[0, 0, 0]]
    assert detail["attributes"]["lattice_vectors"] == [[3, 0, 0], [0, 3, 0], [0, 0, 3]]


@pytest.mark.parametrize(
    "path", ["/api/structures/1/POSCAR", "/api/promoted/structures/1/POSCAR", "/api/structures/1/structure.cif"]
)
def test_native_downloads(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert "attachment" in response.headers["content-disposition"]
    assert "Mg" in response.text
    assert response.headers["x-content-type-options"] == "nosniff"


def test_promoted_download_uses_the_relaxed_geometry(client):
    main = client.get("/optimade/v1/structures/omdb.structures-1-1").json()["data"]
    relaxed = client.get("/optimade/v1/structures/omdb.promoted.structures-1-1").json()["data"]
    assert main["attributes"]["lattice_vectors"][0][0] == 3
    assert relaxed["attributes"]["lattice_vectors"][0][0] == 5
    assert client.get("/api/structures/1/POSCAR").text != client.get("/api/promoted/structures/1/POSCAR").text


def test_legacy_entry_and_missing_database(client, tmp_path):
    assert client.get("/index.php?material=Magnesium").status_code == 200
    assert client.get("/index.php?compound_cid=1").status_code == 200
    assert client.get("/index.php?vasp_sid=1", follow_redirects=False).headers["location"] == "/api/structures/1/POSCAR"
    assert client.get("/index.php?vasp_sid=bad").status_code == 400
    assert client.get("/api/structures/1/secret").status_code == 404
    with pytest.raises(FileNotFoundError, match="build_database"):
        create_app(tmp_path / "missing")


def test_application_closes_database(dataset):
    """The composed lifespan releases the app-owned native database."""
    app = create_app(dataset)
    closed = []
    original = app.state.database.dispose

    def dispose():
        closed.append(True)
        original()

    app.state.database.dispose = dispose
    with TestClient(app) as client:
        assert client.get("/api/materials").status_code == 200
    assert closed == [True]


def test_public_base_url_sets_optimade_links(dataset):
    with TestClient(create_app(dataset, public_base_url="https://omdb.example.org/")) as client:
        response = client.get("/optimade/v1/structures", params={"page_limit": 1}).json()
    assert response["links"]["next"].startswith("https://omdb.example.org/optimade/v1/structures?")
