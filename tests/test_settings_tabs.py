from app.routers.ui import SETTINGS_TAB_KEYS, normalize_settings_tab, settings_path


def test_settings_tab_defaults_and_aliases():
    assert normalize_settings_tab(None) == "device"
    assert normalize_settings_tab("") == "device"
    assert normalize_settings_tab("nope") == "device"
    assert normalize_settings_tab("access") == "device"
    assert normalize_settings_tab("LLM") == "llm"
    assert normalize_settings_tab("backup") == "backup"
    assert normalize_settings_tab("update") == "update"
    assert normalize_settings_tab("filters") == "filters"
    assert normalize_settings_tab("translation") == "translation"
    assert normalize_settings_tab("publication") == "publication"
    assert normalize_settings_tab("notifications") == "notifications"


def test_settings_path_keeps_known_tabs():
    assert settings_path("categories") == "/settings?tab=categories"
    assert settings_path("catalog") == "/settings?tab=catalog"
    assert settings_path("filters") == "/settings?tab=filters"
    assert settings_path("translation") == "/settings?tab=translation"
    assert settings_path("publication") == "/settings?tab=publication"
    assert settings_path("mystery") == "/settings?tab=device"
    assert settings_path("access") == "/settings?tab=device"
    assert SETTINGS_TAB_KEYS == {
        "device",
        "publication",
        "schedule",
        "filters",
        "translation",
        "llm",
        "reader",
        "notifications",
        "categories",
        "catalog",
        "backup",
        "update",
        "about",
    }
