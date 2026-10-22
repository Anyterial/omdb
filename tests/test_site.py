"""Pin the explicitly requested original About information and viewer host."""

import json
from html.parser import HTMLParser
from pathlib import Path

from starlette.testclient import TestClient

from omdb.app import create_app

_FIXTURE = Path(__file__).parent / "fixtures" / "legacy-about.json"
_BLOCKS = {"p", "div", "h3", "h4", "ul", "li", "br"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _AboutContent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.parts = []
        self.links = []
        self.config = []
        self.in_config = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "script" and attributes.get("type") == "application/json":
            self.in_config = True
        if attributes.get("id") == "tab_about" or self.stack:
            if tag not in _VOID:
                self.stack.append(tag)
            if tag in _BLOCKS:
                self.parts.append(" ")
            if tag == "a":
                self.links.append(attributes["href"])

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_config = False
        if self.stack and tag in _BLOCKS:
            self.parts.append(" ")
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]

    def handle_data(self, data):
        if self.stack:
            self.parts.append(data)
        if self.in_config:
            self.config.append(data)


def test_original_about_information_and_viewer_default(dataset, monkeypatch):
    """Render the actual page and retain every original About word and link."""
    monkeypatch.delenv("OMDB_CRYSVIZ_BASE_URL", raising=False)
    expected = json.loads(_FIXTURE.read_text())
    with TestClient(create_app(dataset)) as client:
        response = client.get("/")
        assert response.status_code == 200
        parser = _AboutContent()
        parser.feed(response.text)
        assert " ".join("".join(parser.parts).split()) == expected["text"]
        assert parser.links == expected["links"]
        assert json.loads("".join(parser.config))["crysviz_base_url"] == "https://crysviz.org/"
        assert client.get("/snabelblue.gif").status_code == 200
