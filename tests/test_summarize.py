from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.services.settings import LlmConfig, llm_config, normalize_ollama_root, normalize_provider, set_value
from app.services.summarize import clean_summary, fallback_summary, list_ollama_models, summarize_story, summarize_with_config


def _session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_normalize_ollama_root_strips_v1():
    assert normalize_ollama_root("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"
    assert normalize_ollama_root("") == "http://127.0.0.1:11434"


def test_normalize_provider_defaults_to_openai():
    assert normalize_provider("ollama") == "ollama"
    assert normalize_provider("nope") == "openai"


def test_fallback_when_not_ready():
    config = LlmConfig(
        provider="ollama",
        model="",
        api_key="ollama",
        base_url="http://127.0.0.1:11434/v1",
        ready=False,
        label="Ollama",
    )
    text = summarize_with_config("Headline", "A longer excerpt about the story.", "BBC", config)
    assert "longer excerpt" in text


def test_summarize_story_skips_openai_without_key():
    text = summarize_story("Headline", "Body text that is short.", "BBC", "", "gpt-4o-mini")
    assert text == "Body text that is short."


def test_summarize_uses_ollama_base_url(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["model"] = kwargs["model"]
            return type(
                "R",
                (),
                {"choices": [type("C", (), {"message": type("M", (), {"content": "Local summary."})()})()]},
            )()

    class FakeClient:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("app.services.summarize.OpenAI", FakeClient)
    text = summarize_story(
        "Headline",
        "Enough source text for a local model to summarise.",
        "BBC",
        "ollama",
        "llama3.2",
        base_url="http://127.0.0.1:11434/v1",
    )
    assert text == "Local summary."
    assert captured["kwargs"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert captured["model"] == "llama3.2"


def test_list_ollama_models(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": "llama3.2:latest"}, {"name": "mistral"}]}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url):
            assert url == "http://127.0.0.1:11434/api/tags"
            return FakeResponse()

    monkeypatch.setattr("app.services.summarize.httpx.Client", FakeClient)
    assert list_ollama_models("http://127.0.0.1:11434/v1") == ["llama3.2:latest", "mistral"]


def test_llm_config_ollama_ready():
    db = _session()
    set_value(db, "llm_provider", "ollama")
    set_value(db, "ollama_model", "llama3.2")
    config = llm_config(db)
    assert config.provider == "ollama"
    assert config.ready is True
    assert config.base_url.endswith("/v1")
    assert config.api_key == "ollama"


def test_clean_summary_strips_ai_preamble():
    raw = 'Here is a concise news briefing based on the provided text: "Markets rose after the central bank held rates."'
    assert clean_summary(raw) == "Markets rose after the central bank held rates."
    assert clean_summary("Here's a summary:\nSweden's left-wing bloc leads.") == "Sweden's left-wing bloc leads."
    assert clean_summary("Based on the provided text — China criticised AI competition claims.") == (
        "China criticised AI competition claims."
    )
    plain = "The boy was acquitted on grounds of insanity."
    assert clean_summary(plain) == plain


def test_summarize_story_strips_model_preamble(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            return type(
                "R",
                (),
                {
                    "choices": [
                        type(
                            "C",
                            (),
                            {
                                "message": type(
                                    "M",
                                    (),
                                    {
                                        "content": (
                                            "Here is a concise news briefing based on the provided text: "
                                            "The athlete finished the race."
                                        )
                                    },
                                )()
                            },
                        )()
                    ]
                },
            )()

    class FakeClient:
        def __init__(self, **_kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr("app.services.summarize.OpenAI", FakeClient)
    text = summarize_story("Race", "Source body", "BBC", "key", "gpt-4o-mini")
    assert text == "The athlete finished the race."
    assert "here is" not in text.lower()


def test_fallback_summary_clips():
    long = "word " * 200
    clipped = fallback_summary("Title", long)
    assert clipped.endswith("…")
    assert len(clipped) <= 420
