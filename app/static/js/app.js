function syncTopbarHeight() {
  const topbar = document.querySelector(".topbar");
  if (!topbar) return;
  const height = Math.ceil(topbar.getBoundingClientRect().height);
  document.documentElement.style.setProperty("--topbar-height", `${height}px`);
}
syncTopbarHeight();
window.addEventListener("resize", syncTopbarHeight);
window.addEventListener("orientationchange", syncTopbarHeight);
window.addEventListener("pageshow", syncTopbarHeight);
if (window.visualViewport) {
  window.visualViewport.addEventListener("resize", syncTopbarHeight);
}
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(syncTopbarHeight);
}
if (typeof ResizeObserver === "function") {
  const topbar = document.querySelector(".topbar");
  if (topbar) new ResizeObserver(syncTopbarHeight).observe(topbar);
}

const THEME_KEY = "newscast-theme";
const PALETTE_KEY = "newscast-palette";
const PALETTES = ["default", "ocean", "forest", "slate"];
const THEME_COLORS = {
  default: { light: "#f3eee4", dark: "#12100d" },
  ocean: { light: "#e7eef5", dark: "#0c141c" },
  forest: { light: "#eef1e6", dark: "#10140d" },
  slate: { light: "#ececee", dark: "#121314" },
};

function resolvedTheme(pref) {
  if (pref === "light" || pref === "dark") return pref;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function savedPalette() {
  const value = localStorage.getItem(PALETTE_KEY) || "default";
  return PALETTES.includes(value) ? value : "default";
}

function applyTheme(pref, palette) {
  const theme = resolvedTheme(pref);
  const chosen = palette || savedPalette();
  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.themePref = pref;
  document.documentElement.dataset.palette = chosen;
  document.documentElement.style.colorScheme = theme;
  const meta = document.querySelector("[data-theme-color]");
  if (meta) meta.setAttribute("content", THEME_COLORS[chosen][theme]);
  document.querySelectorAll("[data-theme-set]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.themeSet === pref);
  });
  document.querySelectorAll("[data-palette-set]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.paletteSet === chosen);
  });
}

const savedTheme = localStorage.getItem(THEME_KEY) || "system";
applyTheme(savedTheme);
document.querySelectorAll("[data-theme-set]").forEach((button) => {
  button.addEventListener("click", () => {
    localStorage.setItem(THEME_KEY, button.dataset.themeSet);
    applyTheme(button.dataset.themeSet);
  });
});
document.querySelectorAll("[data-palette-set]").forEach((button) => {
  button.addEventListener("click", () => {
    localStorage.setItem(PALETTE_KEY, button.dataset.paletteSet);
    applyTheme(localStorage.getItem(THEME_KEY) || "system", button.dataset.paletteSet);
  });
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if ((localStorage.getItem(THEME_KEY) || "system") === "system") applyTheme("system");
});

const toastEl = document.querySelector("[data-toast]");
const TOAST_KEY = "newscast-toast";
const TOAST_MS = 4500;

function toast(message, kind) {
  if (!toastEl || !message) return;
  toastEl.textContent = message;
  toastEl.classList.remove("is-ok", "is-error", "is-on");
  if (kind === "error") toastEl.classList.add("is-error");
  else toastEl.classList.add("is-ok");
  toastEl.hidden = false;
  window.requestAnimationFrame(() => {
    toastEl.classList.add("is-on");
  });
  window.clearTimeout(toastEl._timer);
  toastEl._timer = window.setTimeout(() => {
    toastEl.classList.remove("is-on");
    toastEl.hidden = true;
  }, TOAST_MS);
}

function toastAfterReload(message, kind) {
  try {
    sessionStorage.setItem(TOAST_KEY, JSON.stringify({ message, kind: kind || "ok" }));
  } catch {
    toast(message, kind);
  }
}

try {
  const pending = sessionStorage.getItem(TOAST_KEY);
  if (pending) {
    sessionStorage.removeItem(TOAST_KEY);
    const data = JSON.parse(pending);
    toast(data.message, data.kind);
  }
} catch {
  /* ignore a bad stored toast */
}

async function send(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "X-Requested-With": "fetch",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (response.status === 401 && !window.location.pathname.startsWith("/login")) {
    window.location.href = "/login";
    throw new Error("Signed out");
  }
  const data = await response.json().catch(() => ({ ok: response.ok, message: response.statusText }));
  if (!response.ok) {
    throw new Error(data.message || data.detail || "Request failed");
  }
  return data;
}

function applyIngestStatus(data) {
  const pill = document.querySelector("[data-ingest-pill]");
  const refreshBtn = document.querySelector("[data-refresh]");
  if (pill) {
    pill.classList.toggle("is-busy", Boolean(data.running));
    pill.classList.toggle("is-error", !data.running && Boolean(data.last_error));
    if (data.running) {
      pill.textContent = data.progress ? `Refreshing ${data.progress}` : "Refreshing";
    } else {
      pill.textContent = data.last_error ? "Error" : "Idle";
    }
  }
  if (refreshBtn) {
    refreshBtn.disabled = Boolean(data.running);
    refreshBtn.classList.toggle("is-busy", Boolean(data.running));
  }
  document.querySelectorAll("[data-feed-refresh]").forEach((button) => {
    button.disabled = Boolean(data.running);
    button.classList.toggle("is-busy", Boolean(data.running));
  });
}

async function pollIngest() {
  const pill = document.querySelector("[data-ingest-pill]");
  if (!pill) return;
  try {
    applyIngestStatus(await send("/api/ingest/status"));
  } catch {
    /* stay on the last rendered state */
  }
}

if (document.querySelector("[data-ingest-pill]")) {
  window.setInterval(pollIngest, 4000);
}

function askConfirm({ title, body, okLabel }) {
  const sheet = document.querySelector("[data-confirm-sheet]");
  if (!sheet) return Promise.resolve(window.confirm(title));
  return new Promise((resolve) => {
    const titleEl = sheet.querySelector("[data-confirm-title]");
    const bodyEl = sheet.querySelector("[data-confirm-body]");
    const okBtn = sheet.querySelector("[data-confirm-ok]");
    const cancelBtn = sheet.querySelector("[data-confirm-cancel]");
    if (titleEl) titleEl.textContent = title;
    if (bodyEl) {
      bodyEl.hidden = !body;
      bodyEl.textContent = body || "";
    }
    if (okBtn) okBtn.textContent = okLabel || "Remove";
    const finish = (value) => {
      sheet.hidden = true;
      sheet.classList.remove("is-open");
      sheet.removeEventListener("click", onBackdrop);
      okBtn?.removeEventListener("click", onOk);
      cancelBtn?.removeEventListener("click", onCancel);
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onOk = () => finish(true);
    const onCancel = () => finish(false);
    const onBackdrop = (event) => {
      if (event.target === sheet) finish(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") finish(false);
    };
    sheet.addEventListener("click", onBackdrop);
    okBtn?.addEventListener("click", onOk);
    cancelBtn?.addEventListener("click", onCancel);
    document.addEventListener("keydown", onKey);
    sheet.hidden = false;
    sheet.classList.add("is-open");
    okBtn?.focus();
  });
}

function showFormError(form, message) {
  const errorEl = form.querySelector("[data-form-error]");
  if (!errorEl) return;
  errorEl.textContent = message || "";
  errorEl.hidden = !message;
}

document.querySelectorAll("form").forEach((form) => {
  if (form.dataset.bound || form.dataset.native != null) return;
  form.dataset.bound = "1";
  form.addEventListener("submit", async (event) => {
    if (form.method.toLowerCase() !== "post") return;
    event.preventDefault();
    if (form.dataset.confirm) {
      const ok = await askConfirm({
        title: form.dataset.confirm,
        body: form.dataset.confirmDetail || "",
        okLabel: form.dataset.confirmOk || "Remove",
      });
      if (!ok) return;
    }
    const body = new FormData(form);
    const button = form.querySelector("[type=submit]");
    const originalLabel = button?.textContent;
    showFormError(form, "");
    if (button) {
      button.disabled = true;
      if (form.dataset.busyLabel) button.textContent = form.dataset.busyLabel;
    }
    try {
      const data = await send(form.action, { method: "POST", body });
      if (data.reauth) {
        window.location.href = "/login";
        return;
      }
      toastAfterReload(data.message || "Saved", "ok");
      window.location.reload();
    } catch (error) {
      showFormError(form, error.message);
      toast(error.message, "error");
    } finally {
      if (button) {
        button.disabled = false;
        if (originalLabel) button.textContent = originalLabel;
      }
    }
  });
});

const sheet = document.querySelector("[data-sheet]");
const openSheet = document.querySelector("[data-open-sheet]");
const closeSheet = document.querySelector("[data-close-sheet]");
if (sheet && openSheet) {
  openSheet.addEventListener("click", () => {
    sheet.hidden = false;
    sheet.classList.add("is-open");
  });
}
if (sheet && closeSheet) {
  closeSheet.addEventListener("click", () => {
    sheet.hidden = true;
    sheet.classList.remove("is-open");
  });
  sheet.addEventListener("click", (event) => {
    if (event.target === sheet) {
      sheet.hidden = true;
      sheet.classList.remove("is-open");
    }
  });
}

document.querySelectorAll("[data-keep-days]").forEach((select) => {
  const custom = select.closest("form")?.querySelector("[data-custom-expiry]");
  if (!custom) return;
  const sync = () => {
    custom.hidden = select.value !== "0";
  };
  select.addEventListener("change", sync);
  sync();
});

document.querySelectorAll("[data-schedule-mode]").forEach((select) => {
  const custom = select.closest("form")?.querySelector("[data-custom-interval]");
  if (!custom) return;
  const sync = () => {
    custom.hidden = select.value !== "custom";
  };
  select.addEventListener("change", sync);
  sync();
});

const modelSelect = document.querySelector("[data-model-select]");
const modelCustom = document.querySelector("[data-model-custom]");
if (modelSelect && modelCustom) {
  const syncModel = () => {
    modelCustom.hidden = modelSelect.value !== "other";
  };
  modelSelect.addEventListener("change", syncModel);
  syncModel();
}

document.querySelectorAll("[data-llm-provider]").forEach((select) => {
  const form = select.closest("form");
  if (!form) return;
  const sync = () => {
    form.querySelectorAll("[data-provider-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.providerPanel !== select.value;
    });
  };
  select.addEventListener("change", sync);
  sync();
});

document.querySelectorAll("[data-reader-device]").forEach((select) => {
  const form = select.closest("form");
  if (!form) return;
  const host = form.querySelector("[data-reader-host]");
  const folder = form.querySelector("[data-reader-folder]");
  const defaults = {
    xteink: { host: "crosspoint.local", folder: "/News" },
    kobo: { host: "192.168.1.50", folder: "/mnt/onboard/News" },
  };
  const sync = () => {
    const device = select.value;
    form.querySelectorAll("[data-reader-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.readerPanel !== device;
    });
    form.querySelectorAll("[data-reader-hint]").forEach((hint) => {
      hint.hidden = hint.dataset.readerHint !== device;
    });
    const next = defaults[device] || defaults.xteink;
    const prev = device === "kobo" ? defaults.xteink : defaults.kobo;
    if (host) {
      host.placeholder = next.host;
      if (!host.value.trim() || host.value.trim() === prev.host) {
        host.value = device === "kobo" ? "" : next.host;
      }
    }
    if (folder) {
      folder.placeholder = next.folder;
      if (!folder.value.trim() || folder.value.trim() === prev.folder) {
        folder.value = next.folder;
      }
    }
  };
  select.addEventListener("change", sync);
  sync();
});

const loadOllama = document.querySelector("[data-load-ollama]");
if (loadOllama) {
  const form = loadOllama.closest("form");
  const modelInput = form?.querySelector("[data-ollama-model]");
  const pick = form?.querySelector("[data-ollama-pick]");
  const modelSelectOllama = form?.querySelector("[data-ollama-select]");
  loadOllama.addEventListener("click", async () => {
    const base = form?.querySelector("[name=ollama_base_url]")?.value || "";
    loadOllama.disabled = true;
    try {
      const data = await send(`/api/ollama/models?base_url=${encodeURIComponent(base)}`);
      const models = data.models || [];
      if (!models.length) {
        toast("Ollama is running, but no models are installed.", "error");
        return;
      }
      if (modelSelectOllama && pick) {
        modelSelectOllama.innerHTML = '<option value="">Choose…</option>';
        models.forEach((name) => {
          const option = document.createElement("option");
          option.value = name;
          option.textContent = name;
          if (modelInput && modelInput.value === name) option.selected = true;
          modelSelectOllama.appendChild(option);
        });
        pick.hidden = false;
      }
      toast(`Found ${models.length} model${models.length === 1 ? "" : "s"}.`, "ok");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      loadOllama.disabled = false;
    }
  });
  modelSelectOllama?.addEventListener("change", () => {
    if (modelInput && modelSelectOllama.value) modelInput.value = modelSelectOllama.value;
  });
}

function applyChipFilters(root) {
  const selected = {};
  root.querySelectorAll("[data-chip-group]").forEach((group) => {
    const key = group.dataset.filterKey || "category";
    selected[key] = group.querySelector("[data-filter].is-active")?.dataset.filter || "all";
  });
  const category = selected.category || "all";
  const source = selected.source || "all";
  let visible = 0;
  const items = root.querySelectorAll("[data-filter-item]");
  if (items.length) {
    items.forEach((item) => {
      const hide =
        (category !== "all" && item.dataset.category !== category) ||
        (source !== "all" && item.dataset.source !== source);
      item.hidden = hide;
      if (!hide) visible += 1;
    });
    root.querySelectorAll("[data-filter-section]").forEach((section) => {
      const any = [...section.querySelectorAll("[data-filter-item]")].some((item) => !item.hidden);
      section.hidden = !any;
    });
  } else {
    root.querySelectorAll("[data-category]").forEach((item) => {
      const hide =
        category !== "all" &&
        (category === "favourites" ? item.dataset.favourited !== "1" : item.dataset.category !== category);
      item.hidden = hide;
      if (!hide) visible += 1;
    });
  }
  const empty = root.querySelector("[data-filter-empty]");
  if (empty) empty.hidden = visible > 0;
}

function resetFilterScroll() {
  const main = document.querySelector(".main");
  if (main) main.scrollTop = 0;
  window.scrollTo(0, 0);
  document.documentElement.scrollTop = 0;
  document.body.scrollTop = 0;
}

document.querySelectorAll("[data-chip-group]").forEach((group) => {
  const chips = group.querySelectorAll("[data-filter]");
  const root = group.closest("[data-filter-root]") || document;
  chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      chips.forEach((other) => other.classList.toggle("is-active", other === chip));
      applyChipFilters(root);
      resetFilterScroll();
      requestAnimationFrame(resetFilterScroll);
    });
  });
});

const settingsRoot = document.querySelector("[data-settings-tabs]");
if (settingsRoot) {
  const SETTINGS_SAVE_TABS = new Set(["device", "schedule", "filters", "llm", "reader", "update"]);
  const settingsForm = settingsRoot.querySelector("[data-settings]");
  const settingsLede = document.querySelector("[data-settings-lede]");
  const settingsTabField = settingsRoot.querySelector("[data-settings-tab-field]");
  const settingsChips = settingsRoot.querySelectorAll("[data-settings-tab]");

  function showSettingsTab(tab) {
    const next = [...settingsChips].some((chip) => chip.dataset.settingsTab === tab) ? tab : "device";
    settingsChips.forEach((chip) => {
      const on = chip.dataset.settingsTab === next;
      chip.classList.toggle("is-active", on);
      chip.setAttribute("aria-selected", on ? "true" : "false");
    });
    settingsRoot.querySelectorAll("[data-settings-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.settingsPanel !== next;
    });
    if (settingsForm) settingsForm.hidden = !SETTINGS_SAVE_TABS.has(next);
    if (settingsTabField) settingsTabField.value = next;
    const activeChip = [...settingsChips].find((chip) => chip.dataset.settingsTab === next);
    if (settingsLede && activeChip?.dataset.settingsLede) {
      settingsLede.textContent = activeChip.dataset.settingsLede;
    }
    const url = new URL(window.location.href);
    url.searchParams.set("tab", next);
    window.history.replaceState(null, "", url);
    resetFilterScroll();
    requestAnimationFrame(resetFilterScroll);
  }

  settingsChips.forEach((chip) => {
    chip.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      showSettingsTab(chip.dataset.settingsTab);
    });
  });
}

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  const input = button.closest(".password-field")?.querySelector("input");
  if (!input) return;
  const showIcon = button.querySelector("[data-icon-show]");
  const hideIcon = button.querySelector("[data-icon-hide]");
  button.addEventListener("click", () => {
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    button.title = show ? "Hide password" : "Show password";
    if (showIcon) showIcon.hidden = show;
    if (hideIcon) hideIcon.hidden = !show;
  });
});
