"""Phase 6: the serving API.

The most important test asserts an absence: this application must not expose ground truth.
It is a separate app from the evaluation explorer for exactly that reason, and a boundary
nobody tests is a boundary that erodes.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from surveillance.api.app import AlertHub, create_app

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def test_stats(client):
    body = client.get("/api/stats").json()
    assert body["trades"] > 0
    assert set(body["by_method"]) <= {"statistical", "graph"}


def test_alerts_list_and_filters(client):
    everything = client.get("/api/alerts?limit=1000").json()
    assert everything
    assert everything == sorted(everything, key=lambda a: (-a["score"], a["id"]))

    critical = client.get("/api/alerts?severity=critical&limit=50").json()
    assert all(a["severity"] == "critical" for a in critical)

    graph = client.get("/api/alerts?method=graph&limit=50").json()
    assert graph
    assert all("graph" in a["detection_method"] for a in graph)


def test_alert_detail_carries_its_evidence(client):
    top = client.get("/api/alerts?method=graph&limit=1").json()[0]
    detail = client.get(f"/api/alerts/{top['id']}").json()
    assert detail["details"].get("rule"), "an alert must explain itself"
    assert detail["trades"], "a graph alert must cite the trades it is about"
    assert detail["neighbourhood"], "a graph alert must show the network it found"


def test_unknown_alert_is_404(client):
    assert client.get("/api/alerts/99999999").status_code == 404


def test_status_transitions(client):
    alert_id = client.get("/api/alerts?limit=1").json()[0]["id"]
    try:
        assert client.post(f"/api/alerts/{alert_id}/status?status=escalated").status_code == 200
        assert client.get(f"/api/alerts/{alert_id}").json()["status"] == "escalated"
        assert client.get(f"/api/alerts/{alert_id}").json()["resolved_at"] is not None
        assert client.post(f"/api/alerts/{alert_id}/status?status=nonsense").status_code == 400
    finally:
        client.post(f"/api/alerts/{alert_id}/status?status=open")


def test_serving_api_exposes_no_ground_truth(client):
    """The structural claim of the two-app split.

    Every scenario/label endpoint belongs to the evaluation explorer. If one appeared here
    the detection pipeline would be one import away from grading its own homework.
    """
    schema = client.get("/openapi.json").json()
    for path in schema["paths"]:
        assert "scenario" not in path
        assert "ground" not in path
        assert "label" not in path

    body = json.dumps(client.get("/api/alerts?limit=50").json())
    for forbidden in ("scenario_id", "wash_ring_0", "hn_", "expected_layer", "hard_negative"):
        assert forbidden not in body


def test_websocket_delivers_live_events(client):
    alert_id = client.get("/api/alerts?limit=1").json()[0]["id"]
    with client.websocket_connect("/ws/alerts") as ws:
        assert json.loads(ws.receive_text())["event"] == "ready"
        client.post(f"/api/alerts/{alert_id}/status?status=cleared")
        payload = json.loads(ws.receive_text())
        assert payload["event"] == "status"
        assert payload["alert_id"] == alert_id
    client.post(f"/api/alerts/{alert_id}/status?status=open")


def test_hub_drops_for_the_slow_subscriber_only():
    """One stalled browser must not stall the feed for everyone else."""

    async def scenario():
        hub = AlertHub(maxsize=2)
        slow = await hub.connect()
        fast = await hub.connect()
        for i in range(10):
            await hub.publish({"n": i})
        # The slow queue is full and dropped the rest; the fast one is drained as we go.
        assert slow.qsize() == 2
        while not fast.empty():
            fast.get_nowait()
        await hub.publish({"n": "after"})
        assert fast.qsize() == 1, "a full subscriber must not block a healthy one"

    asyncio.run(scenario())


def test_config_advertises_the_explorer_location(client):
    """The dashboard is served from this origin but the explorer lives on another port,
    so the location has to be discoverable rather than assumed."""
    body = client.get("/api/config").json()
    assert body["explorer_url"]
    assert "explorer" not in client.get("/openapi.json").json()["paths"].keys() - {"/api/config"}


def test_explorer_routes_are_not_on_the_serving_api(client):
    """The two apps share an origin in the browser. When they also shared a path prefix,
    /api/stats meant two different things depending on which port answered -- and the
    dataset view silently rendered zeros against the wrong schema."""
    for path in ("/explorer/stats", "/explorer/scenarios", "/explorer/z-histogram"):
        assert client.get(path).status_code == 404
