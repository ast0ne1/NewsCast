from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, Feed, User
from app.services import passwords


def test_two_users_can_share_same_feed_url():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    db = Session(engine)
    a = User(username="alice", password=passwords.hash_password("aaaa"), role="user")
    b = User(username="bob", password=passwords.hash_password("bbbb"), role="user")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)
    db.add(
        Feed(
            user_id=a.id,
            name="Shared",
            url="https://example.com/rss",
            enabled=True,
            type="rss",
            category="news",
        )
    )
    db.add(
        Feed(
            user_id=b.id,
            name="Shared",
            url="https://example.com/rss",
            enabled=True,
            type="rss",
            category="news",
        )
    )
    db.commit()
    alice_feeds = db.query(Feed).filter(Feed.user_id == a.id).all()
    bob_feeds = db.query(Feed).filter(Feed.user_id == b.id).all()
    assert len(alice_feeds) == 1
    assert len(bob_feeds) == 1
    assert alice_feeds[0].url == bob_feeds[0].url
    assert alice_feeds[0].id != bob_feeds[0].id
    db.close()
