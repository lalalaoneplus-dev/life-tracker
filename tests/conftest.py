import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TOKEN = "TESTTOKEN"
SECRET = "TESTSECRET"


@pytest.fixture
def app(tmp_path):
    return create_app(db_path=str(tmp_path / "t.db"), agent_token=TOKEN, session_secret=SECRET)


@pytest.fixture
def agent(app):
    """Full-write client (agent bearer)."""
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


@pytest.fixture
def session(app):
    """Browser-session client (cookie only, read + toggle)."""
    c = TestClient(app)
    r = c.post("/api/login", json={"secret": SECRET})
    assert r.status_code == 200
    return c


@pytest.fixture
def anon(app):
    return TestClient(app)
