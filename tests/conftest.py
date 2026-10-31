import os
import pytest
import uuid
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Configure test environment
os.environ["APP_ENV"] = "test"
os.environ["FIREBASE_PROJECT_ID"] = "test-hisaab-ai-project"
os.environ["DATABASE_URL"] = "sqlite:///./test_expenses.db"

import database
from database import Base, get_db
import main
from main import app
import agent

# Create isolated test SQLite engine
TEST_DB_URL = "sqlite:///./test_expenses.db"
test_engine = create_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

from sqlalchemy import event

@event.listens_for(test_engine, "connect")
def set_test_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()

# Override engine & SessionLocal in database module
database.engine = test_engine
database.SessionLocal = TestingSessionLocal
agent.SessionLocal = TestingSessionLocal

# Registry for mock Firebase tokens in tests
MOCK_FIREBASE_TOKENS = {}


@pytest.fixture(autouse=True)
def mock_firebase_verifier(monkeypatch):
    """
    Transparently intercepts verify_firebase_token_claims in tests.
    If the token exists in MOCK_FIREBASE_TOKENS, returns registered claims (or raises exception).
    Otherwise falls back to the original function for negative/invalid token test cases.
    """
    original_verifier = main.verify_firebase_token_claims

    def _interceptor(token: str):
        if token in MOCK_FIREBASE_TOKENS:
            val = MOCK_FIREBASE_TOKENS[token]
            if isinstance(val, Exception):
                raise val
            return val
        return original_verifier(token)

    monkeypatch.setattr(main, "verify_firebase_token_claims", _interceptor)


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
def make_firebase_token():
    def _make(
        user_id: str,
        provider: str = "google.com",
        email_verified: bool = True,
        display_name: str = None,
        photo_url: str = None,
    ):
        email = user_id.strip().lower()
        token = f"mock-firebase-token-{email}-{uuid.uuid4().hex[:6]}"
        project_id = os.environ.get("FIREBASE_PROJECT_ID", "test-hisaab-ai-project")
        claims = {
            "uid": f"uid-{email}",
            "sub": f"uid-{email}",
            "email": email,
            "email_verified": email_verified,
            "name": display_name or email.split("@")[0].capitalize(),
            "picture": photo_url or f"https://lh3.googleusercontent.com/{email}.jpg",
            "aud": project_id,
            "iss": f"https://securetoken.google.com/{project_id}",
            "firebase": {
                "sign_in_provider": "google.com" if provider in ("google", "google.com") else provider
            },
        }
        MOCK_FIREBASE_TOKENS[token] = claims
        return token
    return _make


@pytest.fixture
def auth_headers(make_firebase_token):
    def _headers(user_id: str, provider: str = "google.com", email_verified: bool = True, token_version: int = 0):
        email = user_id.strip().lower()
        session = TestingSessionLocal()
        try:
            acc = session.query(database.UserAccount).filter(database.UserAccount.user_id == email).first()
            if not acc:
                acc = database.UserAccount(
                    user_id=email,
                    firebase_uid=f"uid-{email}",
                    auth_provider="google" if provider in ("google", "google.com") else "password",
                    display_name=email.split("@")[0].capitalize(),
                    email=email,
                    identity_verified=email_verified,
                )
                session.add(acc)
                session.commit()
        finally:
            session.close()
        token = make_firebase_token(user_id=email, provider=provider, email_verified=email_verified)
        return {"Authorization": f"Bearer {token}"}
    return _headers


