from app.services.translate import (
    _chunks,
    _join_segments,
    _unpack_story,
    looks_untranslated,
    translate_story,
    translate_to_english,
)


def test_join_segments_reads_gtx_payload():
    payload = [[["The house is red.", "Huset er rødt", None, None, 10]], None, "da"]
    assert _join_segments(payload) == "The house is red."


def test_join_segments_reads_chrome_payload():
    assert _join_segments([["The airport closed.", "da"]]) == "The airport closed."
    assert _join_segments(["The airport closed.", "da"]) == "The airport closed."


def test_join_segments_rejects_junk():
    assert _join_segments(None) is None
    assert _join_segments([]) is None
    assert _join_segments("nope") is None


def test_chunks_keeps_short_text():
    assert _chunks("short") == ["short"]


def test_chunks_splits_long_text():
    text = ("word " * 1200).strip()
    parts = _chunks(text)
    assert len(parts) > 1
    assert all(len(part) <= 4200 for part in parts)


def test_looks_untranslated_detects_danish():
    assert looks_untranslated("Broarbejde lukker Storebæltsbroen")
    assert looks_untranslated("Mistanke om drone lukkede lufthavn")
    assert not looks_untranslated("Bridge work briefly closes the Great Belt Bridge")
    assert not looks_untranslated("Sund & Bælt writes that Johan Søe won bronze")


def test_translate_to_english_uses_google_post(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [[["The prime minister resigned.", "Statsministeren gik af", None, None, 3]]]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, params, data):
            assert "translate.googleapis.com" in url
            assert params["tl"] == "en"
            assert data["q"] == "Statsministeren gik af"
            return FakeResponse()

        def get(self, *_args, **_kwargs):
            raise AssertionError("chrome fallback should not run")

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    assert translate_to_english("Statsministeren gik af") == "The prime minister resigned."


def test_translate_falls_back_to_chrome(monkeypatch):
    class Limited:
        status_code = 429

    class Chrome:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [["The airport closed.", "da"]]

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            return Limited()

        def get(self, url, params):
            assert "clients5.google.com" in url
            assert params["q"] == "Lufthavnen lukkede"
            return Chrome()

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.translate.time.sleep", lambda *_args: None)
    assert translate_to_english("Lufthavnen lukkede") == "The airport closed."


def test_translate_story_keeps_original_when_request_fails(monkeypatch):
    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            raise RuntimeError("blocked")

        def get(self, *_args, **_kwargs):
            raise RuntimeError("blocked")

    monkeypatch.setattr("app.services.translate.httpx.Client", FakeClient)
    monkeypatch.setattr("app.services.translate.time.sleep", lambda *_args: None)
    title, excerpt = translate_story("Huset brænder", "Politiet er på vej.")
    assert title == "Huset brænder"
    assert excerpt == "Politiet er på vej."


def test_unpack_story_splits_markers():
    assert _unpack_story("[[T]]\nThe house\n[[B]]\nPolice are coming.", "Huset", "Politiet") == (
        "The house",
        "Police are coming.",
    )


def test_translate_empty_returns_empty():
    assert translate_to_english("  ") == ""
