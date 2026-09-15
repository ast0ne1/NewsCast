from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_db
from app.models import Base, User
from app.routers import ui
from app.services import passwords, users


def _session() -> Session:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def _client(db: Session) -> TestClient:
    app = FastAPI()
    app.include_router(ui.public)

    def override():
        yield db

    app.dependency_overrides[get_db] = override
    return TestClient(app, follow_redirects=False)


def test_login_token_works_once_then_expires():
    db = _session()
    user = User(username="erin", password=passwords.hash_password("eeee"), role="user", active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = users.issue_login_token(db, user)
    client = _client(db)

    first = client.get(f"/login/token/{token}")
    assert first.status_code == 303
    assert "newscast=" in first.headers.get("set-cookie", "")

    second = client.get(f"/login/token/{token}")
    assert second.status_code == 401


def test_login_token_rejects_expired():
    db = _session()
    user = User(username="frank", password=passwords.hash_password("ffff"), role="user", active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    token = users.issue_login_token(db, user)
    user.login_token_expires = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    client = _client(db)
    response = client.get(f"/login/token/{token}")
    assert response.status_code == 401
