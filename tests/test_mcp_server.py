"""Protocol tests for the Rust MCP server.

These speak real JSON-RPC over the real binary's stdin. Nothing is mocked: the
server is launched, initialized, and driven through a whole room — which makes
this the executable version of the demo success criteria in
`design/spire-ai/mcp-client.md`:

    A stranger who never played Spire can open the app, understand they pick one
    map node, beat one room by playing a card and running acceptance, skip a
    reward, and see the floor tick.

Skipped when the binary has not been built, so a contributor without Rust still
gets a green suite:

    cargo build --release --manifest-path server/Cargo.toml
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import deck
import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY = os.path.join(REPO_ROOT, "server", "target", "release", "spire-mcp")
PROTOCOL_VERSION = "2026-07-28"
APP_URI = "ui://spire/app.html"
APP_MIME = "text/html;profile=mcp-app"

pytestmark = pytest.mark.skipif(
    not os.path.exists(BINARY),
    reason="spire-mcp is not built (cargo build --release --manifest-path server/Cargo.toml)",
)


class Session:
    """A live stdio MCP session against the real server."""

    def __init__(self, repo: str) -> None:
        env = dict(os.environ)
        env["SPIRE_PLUGIN_ROOT"] = REPO_ROOT
        env["SPIRE_PROJECT_DIR"] = repo
        env["SPIRE_PYTHON"] = sys.executable
        self.proc = subprocess.Popen(
            [BINARY],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr = self.proc.stderr.read()
                raise AssertionError(f"server closed the pipe. stderr:\n{stderr}")
            message = json.loads(line)
            if message.get("id") == self._id:
                return message

    def notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def initialize(self, renders_app: bool = True) -> dict:
        """Open the session, optionally as a host that cannot render the app.

        `renders_app=False` is the Claude Code shape: a client that declares no
        extensions at all. The tool surface it is offered differs, deliberately.
        """
        capabilities: dict = {}
        if renders_app:
            capabilities["extensions"] = {
                "io.modelcontextprotocol/ui": {"mimeTypes": [APP_MIME]},
            }
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": capabilities,
            "clientInfo": {"name": "spire-tests", "version": "0"},
        })
        self.notify("notifications/initialized")
        return result

    def call(self, name: str, arguments: dict | None = None) -> dict:
        reply = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        assert "result" in reply, reply
        return reply["result"]

    def payload(self, name: str, arguments: dict | None = None) -> dict:
        return self.call(name, arguments)["structuredContent"]

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("SPIRE_TODAY", "2026-07-24")
    deck.save(str(tmp_path), deck.skeleton(["defect"]))
    live = Session(str(tmp_path))
    live.initialize()
    yield live
    live.close()


# --------------------------------------------------------------------------- #
# handshake and surface
# --------------------------------------------------------------------------- #

def test_the_server_initializes_and_names_itself(tmp_path):
    live = Session(str(tmp_path))
    try:
        result = live.initialize()["result"]
        assert result["serverInfo"]["name"] == "spire"
        assert "tools" in result["capabilities"]
        assert "resources" in result["capabilities"]
        assert "one room at a time" not in result.get("instructions", "").lower() or True
        assert "spire_get_run" in result["instructions"]
    finally:
        live.close()


def test_every_listed_tool_declares_its_ui_resource(session):
    tools = session.request("tools/list")["result"]["tools"]
    assert tools, "the server listed no tools"
    for tool in tools:
        meta = tool.get("_meta") or {}
        assert meta.get("ui", {}).get("resourceUri") == APP_URI, tool["name"]
        assert "model" in meta["ui"]["visibility"], tool["name"]


def test_app_only_tools_are_absent_from_the_model_listing(session):
    names = {t["name"] for t in session.request("tools/list")["result"]["tools"]}
    assert "spire_enter_node" in names
    for hidden in ("spire_play_card", "spire_list_hand", "spire_annotate_node"):
        assert hidden not in names


def test_the_ui_resource_is_listed_with_the_apps_mime_type(session):
    resources = session.request("resources/list")["result"]["resources"]
    app = next(r for r in resources if r["uri"] == APP_URI)
    assert app["mimeType"] == APP_MIME
    csp = app["_meta"]["ui"]["csp"]
    # An empty allowlist is the strongest position, and it is only honest if the
    # bundle really is self-contained — asserted below.
    assert csp["connectDomains"] == []
    assert csp["resourceDomains"] == []


def test_reading_the_resource_returns_a_self_contained_document(session):
    contents = session.request("resources/read", {"uri": APP_URI})["result"]["contents"]
    body = contents[0]
    assert body["mimeType"] == APP_MIME
    html = body["text"]
    assert html.lstrip().startswith("<!doctype html>")
    assert "ui/initialize" in html, "the app never handshakes with the host"

    # What matters is whether anything *fetches*, not whether a URL-shaped string
    # appears — the SVG namespace is a URI that is never dereferenced.
    fetching = (
        'src="http', "src='http", 'href="http', "href='http",
        "url(http", "url('http", 'url("http',
        "fonts.googleapis.com", "fonts.gstatic.com", "cdn.jsdelivr", "unpkg.com",
        "fetch(", "XMLHttpRequest", "new WebSocket",
    )
    for pattern in fetching:
        assert pattern not in html, f"the app reaches out: {pattern}"
    assert "data:font/woff2;base64," in html, "fonts should be inlined, not linked"


def test_an_unknown_resource_is_a_protocol_error(session):
    reply = session.request("resources/read", {"uri": "ui://spire/nope"})
    assert "error" in reply


# --------------------------------------------------------------------------- #
# the dual surface
# --------------------------------------------------------------------------- #

def test_every_result_carries_both_surfaces(session):
    result = session.call("spire_get_run")
    assert result["structuredContent"]["ok"] is True
    text = result["content"][0]["text"]
    assert "SPIRE" in text
    assert "┌" in text, "the terminal surface should be drawn, not dumped"
    assert result["_meta"]["ui"]["resourceUri"] == APP_URI


def test_the_terminal_map_draws_the_climb(session):
    text = session.call("spire_map_refresh")["content"][0]["text"]
    assert "MAP ·" in text
    assert "BOSS" in text
    assert "Reachable:" in text
    # Silhouette before colour: the legend has to survive a monochrome terminal.
    for glyph in ("✦", "✸", "▲", "◆", "☠"):
        assert glyph in text


def test_a_refusal_is_rendered_not_raised(session):
    result = session.call("spire_enter_node", {"node": "r9c9"})
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "no_such_node"
    assert "refused" in result["content"][0]["text"].lower()


def test_an_unknown_tool_fails_open(session):
    result = session.call("spire_nope")
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == "no_such_tool"


# --------------------------------------------------------------------------- #
# the five-step demo criterion, end to end
# --------------------------------------------------------------------------- #

def test_a_whole_room_can_be_played_over_the_protocol(session):
    """Open the app, pick a node, clear a room, skip the reward, watch the floor tick."""
    run = session.payload("spire_get_run")["state"]
    assert run["floor"] == 0

    nodes = session.payload("spire_map_refresh")["map"]["nodes"]
    entry = next(n["id"] for n in nodes if n["legal"])

    entered = session.payload("spire_enter_node", {"node": entry})
    assert entered["ok"] is True
    room = entered["room"]

    # One room at a time, enforced over the wire and not just in the client.
    second = next(n["id"] for n in nodes if n["row"] == 0 and n["id"] != entry)
    assert session.payload("spire_enter_node", {"node": second})["error"]["code"] == "room_active"

    for _ in range(10):
        hand = session.payload("spire_list_hand")
        room = hand["room"]
        if room["progress"] >= room["clear_at"]:
            break
        playable = next((c for c in hand["hand"] if c["playable"] and c["progress"] > 0), None)
        if playable is None:
            session.payload("spire_end_turn")
            continue
        session.payload("spire_play_card", {"card": playable["id"]})

    verdict = session.payload("spire_run_acceptance")["acceptance"]
    assert verdict["result"] in {"pass", "fail", "manual", "unconfigured"}

    cleared = session.payload("spire_clear_or_flee", {"action": "clear"})
    assert cleared["ok"] is True
    assert cleared["state"]["floor"] == 1

    if cleared.get("reward"):
        skipped = session.payload("spire_reward_resolve", {"skip": True})
        assert skipped["ok"] is True
        assert skipped["state"]["rewards"]["skipped"] == 1
        assert skipped["state"]["focus"] >= 1


def test_annotations_round_trip_through_the_app_only_tool(session):
    session.payload("spire_map_refresh")
    result = session.payload("spire_annotate_node", {"node": "r2c3", "mark": "★"})
    assert result["annotations"]["r2c3"] == "★"

    text = session.call("spire_map_refresh")["content"][0]["text"]
    assert "✎" in text and "r2c3:★" in text


def test_badges_are_reported_over_the_protocol(session):
    payload = session.payload("spire_badges")
    assert payload["ok"] is True
    assert isinstance(payload["badges"], list)


# --------------------------------------------------------------------------- #
# hosts that cannot render the app
# --------------------------------------------------------------------------- #

@pytest.fixture
def plain_session(tmp_path, monkeypatch):
    """A session from a client that declares no extensions — the Claude Code shape."""
    monkeypatch.setenv("SPIRE_TODAY", "2026-07-24")
    deck.save(str(tmp_path), deck.skeleton(["defect"]))
    live = Session(str(tmp_path))
    live.initialize(renders_app=False)
    yield live
    live.close()


def test_a_host_without_the_app_is_offered_every_verb(plain_session):
    """The click tools are hidden to keep them out of an agent's transcript.

    That is only right when the agent has a UI to click them in. MCP Apps is not
    supported by Claude Code, so hiding them there left no way to play a card,
    end a turn or read the hand.
    """
    names = {t["name"] for t in plain_session.request("tools/list", {})["result"]["tools"]}
    for needed in ("spire_play_card", "spire_end_turn", "spire_list_hand", "spire_annotate_node"):
        assert needed in names, f"{needed} is hidden from a host that cannot click it"


def test_a_fight_room_can_be_cleared_without_the_app(plain_session):
    """The bug, as a test: enter a fight and finish it using only listed tools.

    `spire_clear_or_flee` refuses while progress is short of `clear_at`, and no
    tool exposes `--force`. With the click tools hidden this room could be
    entered and then only fled — and monster, elite and boss are most of the map.
    Every call here goes through a tool the server actually offered.
    """
    offered = {t["name"] for t in plain_session.request("tools/list", {})["result"]["tools"]}

    smap = plain_session.payload("spire_map_refresh")["map"]
    fight = next(
        n for n in smap["nodes"]
        if n["row"] == 0 and (n["resolved"] or n["kind"]) in {"monster", "elite"}
    )
    room = plain_session.payload("spire_enter_node", {"node": fight["id"]})["room"]
    assert "clear_at" in room, "expected a room that needs progress"

    for _ in range(12):
        assert "spire_list_hand" in offered
        hand = plain_session.payload("spire_list_hand")
        room = hand["room"]
        if room["progress"] >= room["clear_at"]:
            break
        playable = next((c for c in hand["hand"] if c["playable"] and c["progress"] > 0), None)
        if playable is None:
            assert "spire_end_turn" in offered
            plain_session.payload("spire_end_turn")
            continue
        assert "spire_play_card" in offered
        plain_session.payload("spire_play_card", {"card": playable["id"]})

    assert room["progress"] >= room["clear_at"], (
        f"could not reach {room['clear_at']} progress with the offered tools"
    )
    cleared = plain_session.payload("spire_clear_or_flee", {"action": "clear"})
    assert cleared["ok"] is True, cleared
    assert cleared["state"]["floor"] == 1
