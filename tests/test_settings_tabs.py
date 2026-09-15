from app.routers.ui import (
    ADMIN_ONLY_SETTINGS_TABS,
    SETTINGS_TAB_KEYS,
    normalize_settings_tab,
    normalize_settings_tab_for_role,
    settings_path,
    settings_tabs_for,
)


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
        "users",
        "backup",
        "update",
        "about",
    }


def test_non_admin_settings_tabs_hide_household_controls():
    keys = {key for key, _label in settings_tabs_for("user", can_use_ntfy=True)}
    assert "llm" not in keys
    assert "backup" not in keys
    assert "users" not in keys
    assert "catalog" not in keys
    assert "schedule" not in keys
    assert "update" not in keys
    assert "publication" in keys
    assert "notifications" in keys
    assert "about" in keys
    assert ADMIN_ONLY_SETTINGS_TABS.isdisjoint(keys)
    assert normalize_settings_tab_for_role("llm", "user") == "device"
    assert normalize_settings_tab_for_role("reader", "user") == "reader"
    assert normalize_settings_tab_for_role("llm", "admin") == "llm"


def test_notifications_tab_requires_ntfy_permission():
    without = {key for key, _ in settings_tabs_for("user", can_use_ntfy=False)}
    with_ntfy = {key for key, _ in settings_tabs_for("user", can_use_ntfy=True)}
    assert "notifications" not in without
    assert "notifications" in with_ntfy
    assert normalize_settings_tab_for_role("notifications", "user", can_use_ntfy=False) == "device"
    assert normalize_settings_tab_for_role("notifications", "user", can_use_ntfy=True) == "notifications"
    assert "notifications" in {key for key, _ in settings_tabs_for("admin")}
