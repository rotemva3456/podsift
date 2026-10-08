"""companion/sealed.py: the one sealing primitive shared by providers/store.py (the AI key) and
feeds.py (the PodFetch key). The critical property: moving providers/store.py's own _stream/_tag
onto this module, keyed by label="ai-settings", must derive the exact same bytes it always did, so
an AI key sealed before this split still opens after it."""
from __future__ import annotations

import stat

from companion import sealed
from companion.providers import store

SECRET = b"0" * 32

# Sealed by providers/store.py's seal() before it was rewritten to call sealed.py (git history:
# the commit right before "one copy of the sealing code"), with SECRET above, key
# "a-known-api-key-0123456789" and origin "https://api.groq.com:443". If this ever stops opening,
# the refactor changed the derived bytes and every AI key ever saved would need re-pasting.
PRE_REFACTOR_SEALED = (
    "v1.AZZPuX5T3fuH1-67q7UlB_iWUtiJPcZEwVNDewNTxEKkXCpEYqvIkYqEXVJtT0eb5dqfQzO3r-JXWZCXJ224ZFGo1"
    "GbrGNBwQ6Q_EEEO9J_GgTdUGOg4NvZMxUhu7AsUOw_HYdofLAnLFn_efL9Y8ajmZaRec74_NBUO")


def test_a_value_sealed_before_the_move_to_sealed_py_still_opens():
    assert store.unseal(SECRET, PRE_REFACTOR_SEALED) == ("a-known-api-key-0123456789", "https://api.groq.com:443")
    assert sealed.unseal(SECRET, "ai-settings", PRE_REFACTOR_SEALED) == (
        "a-known-api-key-0123456789", "https://api.groq.com:443")


def test_store_seal_and_sealed_seal_derive_the_same_bytes_for_the_ai_settings_label():
    # store.py's seal/unseal are thin calls onto sealed.py with label="ai-settings" baked in.
    made_by_store = store.seal(SECRET, "fresh-key", "https://api.groq.com:443")
    assert sealed.unseal(SECRET, "ai-settings", made_by_store) == ("fresh-key", "https://api.groq.com:443")
    made_directly = sealed.seal(SECRET, "ai-settings", "fresh-key", "https://api.groq.com:443")
    assert store.unseal(SECRET, made_directly) == ("fresh-key", "https://api.groq.com:443")


def test_seal_unseal_round_trip():
    value = sealed.seal(SECRET, "feed", "podfetch-api-key", "http://podfetch.test:80")
    assert sealed.unseal(SECRET, "feed", value) == ("podfetch-api-key", "http://podfetch.test:80")


def test_a_different_label_with_the_same_secret_cannot_open_it():
    value = sealed.seal(SECRET, "feed", "podfetch-api-key", "http://podfetch.test:80")
    assert sealed.unseal(SECRET, "ai-settings", value) is None


def test_a_different_secret_cannot_open_it():
    value = sealed.seal(SECRET, "feed", "podfetch-api-key", "http://podfetch.test:80")
    assert sealed.unseal(b"1" * 32, "feed", value) is None


def test_garbage_never_raises():
    assert sealed.unseal(SECRET, "feed", "") is None
    assert sealed.unseal(SECRET, "feed", "not sealed at all") is None
    assert sealed.unseal(SECRET, "feed", "v1.####") is None


def test_two_labels_get_two_independent_secret_files(tmp_path):
    ai_key = sealed.secret(tmp_path, "ai-settings", create=True)
    feed_key = sealed.secret(tmp_path, "feed", create=True)
    assert ai_key != feed_key
    assert {p.name for p in tmp_path.iterdir()} == {"ai-settings.secret", "feed.secret"}
    for name in ("ai-settings.secret", "feed.secret"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_secret_is_stable_across_calls_and_created_only_once(tmp_path):
    first = sealed.secret(tmp_path, "feed", create=True)
    second = sealed.secret(tmp_path, "feed", create=True)
    assert first == second and first is not None
    assert sealed.secret(tmp_path, "feed", create=False) == first


def test_no_secret_file_and_no_create_is_none_not_an_error(tmp_path):
    assert sealed.secret(tmp_path, "feed", create=False) is None
