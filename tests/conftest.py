import os
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Configure test environment
os.environ["APP_ENV"] = "test"
os.environ["SESSION_SECRET"] = "test-secret-key-32-bytes-long-for-tests-12345"
os.environ["DATABASE_URL"] = "sqlite:///./test_expenses.db"

import database
from database import Base, get_db
import main
from main import app, create_session_token
import agent

# Create isolated test SQLite engine
TEST_DB_URL = "sqlite:///./test_expenses.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Override engine & SessionLocal in database module
database.engine = test_engine
database.SessionLocal = TestingSessionLocal
agent.SessionLocal = TestingSessionLocal


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    if os.path.exists("./test_expenses.db"):
        try:
            os.remove("./test_expenses.db")
        except Exception:
            pass


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_token():
    def _make(user_id: str, provider: str = "credentials"):
        return create_session_token(user_id=user_id, provider=provider)
    return _make


@pytest.fixture
def auth_headers(make_token):
    def _headers(user_id: str, provider: str = "credentials"):
        token = make_token(user_id, provider)
        return {"Authorization": f"Bearer {token}"}
    return _headers
