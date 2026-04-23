"""Tests for kernel.clients — MirrorClient and SquadClient HTTP wrappers."""

from unittest.mock import patch, MagicMock

from kernel.clients import MirrorClient, SquadClient


def _mock_response(status_code: int, json_data):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# MirrorClient
# ---------------------------------------------------------------------------

def test_mirror_client_store_success():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    mock_resp = _mock_response(200, {"status": "success"})
    with patch("requests.post", return_value=mock_resp):
        result = client.store({"text": "hello", "agent": "test"})
    assert result == {"status": "success"}


def test_mirror_client_store_returns_empty_on_error():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    with patch("requests.post", side_effect=Exception("connection refused")):
        result = client.store({"text": "hello"})
    assert result == {}


def test_mirror_client_search_success():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    payload = {"results": [{"id": "abc", "text": "result"}]}
    mock_resp = _mock_response(200, payload)
    with patch("requests.post", return_value=mock_resp):
        results = client.search("test query", top_k=3)
    assert len(results) == 1
    assert results[0]["id"] == "abc"


def test_mirror_client_search_returns_empty_list_when_no_results():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    mock_resp = _mock_response(200, {"results": []})
    with patch("requests.post", return_value=mock_resp):
        results = client.search("test query")
    assert results == []


def test_mirror_client_returns_empty_on_error():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    with patch("requests.post", side_effect=Exception("connection refused")):
        results = client.search("test")
    assert results == []


def test_mirror_client_recent_success():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    payload = {"engrams": [{"id": "e1", "text": "memory"}]}
    mock_resp = _mock_response(200, payload)
    with patch("requests.get", return_value=mock_resp):
        engrams = client.recent("brain", limit=5)
    assert len(engrams) == 1
    assert engrams[0]["id"] == "e1"


def test_mirror_client_recent_returns_empty_on_error():
    client = MirrorClient(base_url="http://test:8844", token="sk-test")
    with patch("requests.get", side_effect=Exception("timeout")):
        result = client.recent("brain")
    assert result == []


# ---------------------------------------------------------------------------
# SquadClient
# ---------------------------------------------------------------------------

def test_squad_client_health():
    client = SquadClient(base_url="http://test:8060")
    mock_resp = _mock_response(200, {"status": "ok"})
    with patch("requests.get", return_value=mock_resp):
        result = client.health()
    assert result["status"] == "ok"


def test_squad_client_health_returns_empty_on_error():
    client = SquadClient(base_url="http://test:8060")
    with patch("requests.get", side_effect=Exception("timeout")):
        result = client.health()
    assert result == {}


def test_squad_client_list_tasks_success():
    client = SquadClient(base_url="http://test:8060")
    task_list = [{"id": "t-001", "title": "Fix bug", "status": "backlog"}]
    mock_resp = _mock_response(200, task_list)
    with patch("requests.get", return_value=mock_resp):
        result = client.list_tasks()
    assert len(result) == 1
    assert result[0]["id"] == "t-001"


def test_squad_client_list_tasks_empty_on_error():
    client = SquadClient(base_url="http://test:8060")
    with patch("requests.get", side_effect=Exception("timeout")):
        result = client.list_tasks()
    assert result == []


def test_squad_client_create_task_success():
    client = SquadClient(base_url="http://test:8060")
    payload = {"id": "t-002", "title": "New task"}
    mock_resp = _mock_response(200, payload)
    with patch("requests.post", return_value=mock_resp):
        result = client.create_task(payload)
    assert result["id"] == "t-002"


def test_squad_client_create_task_returns_empty_on_error():
    client = SquadClient(base_url="http://test:8060")
    with patch("requests.post", side_effect=Exception("503 Service Unavailable")):
        result = client.create_task({"title": "test"})
    assert result == {}


def test_squad_client_claim_task_success():
    client = SquadClient(base_url="http://test:8060")
    mock_resp = _mock_response(200, {"id": "t-001", "assignee": "kasra"})
    with patch("requests.post", return_value=mock_resp):
        result = client.claim_task("t-001", "kasra")
    assert result["assignee"] == "kasra"


def test_squad_client_complete_task_success():
    client = SquadClient(base_url="http://test:8060")
    mock_resp = _mock_response(200, {"id": "t-001", "status": "done"})
    with patch("requests.post", return_value=mock_resp):
        result = client.complete_task("t-001", {"output": "deployed"})
    assert result["status"] == "done"
