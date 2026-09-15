from app.services import i18n


def setup_function():
    i18n.clear_cache()


def test_missing_key_returns_key():
    assert i18n.t("Totally missing msgid", lang="en") == "Totally missing msgid"
    assert i18n.t("Totally missing msgid", lang="es") == "Totally missing msgid"


def test_es_partial_returns_spanish_for_briefing():
    assert i18n.t("Briefing", lang="es") == "Resumen"
    assert i18n.t("Feeds", lang="es") == "Fuentes"
    # Absent in es.json → English fallback
    assert i18n.t("Catalog", lang="es") == "Catalog"
    assert i18n.t("Idle", lang="es") == "Idle"


def test_format_kwargs():
    assert i18n.t("Hello, {name}", lang="en", name="Ada") == "Hello, Ada"
    assert i18n.t("Remove {name}?", lang="en", name="BBC") == "Remove BBC?"
