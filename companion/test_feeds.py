"""companion/feeds.py: the token and the sealed PodFetch key, and the address resolution the feed
routes build every link from. The HTTP surface (companion/routes/test_feed.py) covers the rest."""
from __future__ import annotations

import stat

from starlette.requests import Request

from companion import feeds, sealed


def _request(headers: dict[str, str]) -> Request:
    scope = {"type": "http", "method": "GET", "path": "/x", "query_string": b"", "scheme": "http",
             "server": ("internal", 8000), "client": ("test", 1234),
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    return Request(scope)


def test_a_fresh_token_unseals_back_to_the_same_key(tmp_path):
    import sqlite3
    from companion import db as database
    db = database.connect(tmp_path / "c.db")
    database.migrate(tmp_path / "c.db")
    token = feeds.create_token(db, tmp_path, "default", "http://podfetch.test", "secret-api-key")
    assert len(token) >= 32
    assert feeds.podfetch_key_for(db, tmp_path, "default", "http://podfetch.test") == "secret-api-key"


def test_the_secret_file_is_created_private(tmp_path):
    from companion import db as database
    db = database.connect(tmp_path / "c.db")
    database.migrate(tmp_path / "c.db")
    feeds.create_token(db, tmp_path, "default", "http://podfetch.test", "k")
    mode = stat.S_IMODE((tmp_path / sealed.secret_file_name(feeds.LABEL)).stat().st_mode)
    assert mode == 0o600


def test_no_api_key_needed_when_podfetch_has_no_login(tmp_path):
    """create_token(..., api_key=None): PodFetch has no login, so there is nothing to seal and no
    secret file is even created -- show_feed_xml then fetches PodFetch's RSS with no key."""
    from companion import db as database
    db = database.connect(tmp_path / "c.db")
    database.migrate(tmp_path / "c.db")
    token = feeds.create_token(db, tmp_path, "default", "http://podfetch.test", None)
    assert feeds.user_for_token(db, token) == "default"
    assert feeds.podfetch_key_for(db, tmp_path, "default", "http://podfetch.test") is None
    assert not (tmp_path / sealed.secret_file_name(feeds.LABEL)).exists()


def test_a_key_sealed_for_one_podfetch_address_is_not_reused_for_another(tmp_path):
    from companion import db as database
    db = database.connect(tmp_path / "c.db")
    database.migrate(tmp_path / "c.db")
    feeds.create_token(db, tmp_path, "default", "http://podfetch.test", "k")
    assert feeds.podfetch_key_for(db, tmp_path, "default", "http://other-host.test") is None
    assert feeds.podfetch_key_for(db, tmp_path, "default", "http://podfetch.test") == "k"


def test_user_for_token_rejects_wrong_and_malformed_tokens(tmp_path):
    from companion import db as database
    db = database.connect(tmp_path / "c.db")
    database.migrate(tmp_path / "c.db")
    token = feeds.create_token(db, tmp_path, "alice", "http://podfetch.test", "k")
    assert feeds.user_for_token(db, token) == "alice"
    assert feeds.user_for_token(db, "") is None
    assert feeds.user_for_token(db, "too-short") is None
    assert feeds.user_for_token(db, "x" * 500) is None
    assert feeds.user_for_token(db, token[:-1] + ("a" if token[-1] != "a" else "b")) is None


def test_a_new_token_replaces_the_old_one_for_the_same_user(tmp_path):
    from companion import db as database
    db = database.connect(tmp_path / "c.db")
    database.migrate(tmp_path / "c.db")
    first = feeds.create_token(db, tmp_path, "default", "http://podfetch.test", "k1")
    second = feeds.create_token(db, tmp_path, "default", "http://podfetch.test", "k2")
    assert feeds.user_for_token(db, first) is None
    assert feeds.user_for_token(db, second) == "default"
    assert feeds.podfetch_key_for(db, tmp_path, "default", "http://podfetch.test") == "k2"


def test_public_base_url_prefers_forwarded_headers_over_host(tmp_path):
    plain = _request({"host": "internal:8000"})
    assert feeds.public_base_url(plain) == "http://internal:8000"
    proxied = _request({"host": "internal:8000", "x-forwarded-host": "mypodcasts.example.com",
                        "x-forwarded-proto": "https"})
    assert feeds.public_base_url(proxied) == "https://mypodcasts.example.com"


def test_forwarded_host_headers_only_carries_the_routing_headers(tmp_path):
    request = _request({"host": "internal:8000", "x-forwarded-proto": "https", "authorization": "Bearer secret",
                        "cookie": "session=abc"})
    headers = feeds.forwarded_host_headers(request)
    assert headers == {"host": "internal:8000", "x-forwarded-proto": "https"}
