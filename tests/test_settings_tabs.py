from app.routers.ui import SETTINGS_TAB_KEYS, normalize_settings_tab, settings_path


def test_settings_tab_defaults_and_aliases():
    assert normalize_settings_tab(None) == "access"
    assert normalize_settings_tab("") == "access"
    assert normalize_settings_tab("nope") == "access"
    assert normalize_settings_tab("LLM") == "llm"
    assert normalize_settings_tab("backup") == "backup"
    assert normalize_settings_tab("update") == "update"
    assert normalize_settings_tab("filters") == "filters"


def test_settings_path_keeps_known_tabs():
    assert settings_path("categories") == "/settings?tab=categories"
    assert settings_path("filters") == "/settings?tab=filters"
    assert settings_path("mystery") == "/settings?tab=access"
    assert SETTINGS_TAB_KEYS == {
        "access",
        "schedule",
        "filters",
        "llm",
        "reader",
        "categories",
        "backup",
        "update",
        "about",
    }
