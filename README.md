# NewsCast

A Python news aggregator for a home Raspberry Pi or a Windows machine on your LAN. It fetches the sources you choose, writes a short briefing, and sends that briefing to an e-reader.

The intended reader paths are **CrossPoint** (Xteink) and **KOReader** (Kobo) over OPDS. A Sync-style API is still there for patched Xteink firmware.

Default login: **admin** / **admin** on the in-page sign-in screen. Change it on Settings after first launch. Passwords are hashed with argon2id; login attempts are rate-limited.

<p align="center">
  <img src="docs/screenshots/login.png?v=0.0.0.8" alt="NewsCast sign-in screen on a phone" width="280" />
</p>

## What it does

- Fetches RSS on a schedule, or scrapes a website when no usable RSS is found
- Deduplicates the same story across outlets
- Writes a concise briefing (OpenAI or a local Ollama model; otherwise extracted text)
- Scores story importance and can keep only items above a threshold
- Optionally allocates Briefing and daily-paper slots by category percentage, and can publish separate per-category OPDS papers
- Stores stories in SQLite and drops unfavourited ones after 7 days
- Serves a mobile-first web UI on the LAN (light, dark, or match the device; English UI with a Spanish scaffold)
- Optional household accounts with separate feeds, papers, Send library, OPDS catalogs, and ntfy topics (shared URL fetch cache)
- Caches each publication’s icon when a source or saved article is added
- Exposes an OPDS catalog for CrossPoint and KOReader (`/opds` for admin, `/opds/u/<username>` per household member), plus JSON / TXT / EPUB briefing downloads (EPUB/TXT freeze at the daily publish time)
- Queues EPUB or PDF files as-is for the next reader sync (not summarised)
- Optional ntfy phone alerts when today’s paper is published and/or reaches the reader (admin always; other users only when allowed)
- Opt-in LAN HTTPS with an in-app local CA (same port as HTTP); Secure cookies when enabled
- Admin backup/restore of the database, settings, library, briefings, feed cache, and TLS certificates

## Web UI

| Tab | What it is for |
| --- | --- |
| **Briefing** | Today or Yesterday, category filters, a star to keep a story past expiry, and a bookmark to save it as a long-read |
| **Saved** | Paste a one-off article URL. NewsCast scrapes the full text, keeps it for 7 days or a date you pick, and includes it in the next briefing |
| **Search** | Find stories, favourites, and Saved long-reads in the SQLite store |
| **Send** | Upload an EPUB or PDF. Check the reader when you want, then push now or queue until it is on Wi-Fi. Waiting transfers sit above Your files |
| **Feeds** | Your sources: Enabled / Disabled, mute for 24 hours, health badge, Global vs Custom schedule, keywords, Summarise vs Full article, Translate into your target language, and Add custom when allowed |
| **Catalog** | Browsable library of World News, Nordic, Australia, culture, tech, science, and other sources. Non-admins only see sources the admin approved. Import or export a JSON package (admin). Tap Add; use plus only for a source that is not listed |
| **Status** | Delivery and health, Check reader plus push / publish controls, your personal OPDS catalog URL (`/opds/u/<username>`), downloads, the pending file queue, and a QR code to open or add this copy on an iPhone home screen |
| **Settings** | Tabs for General (palette, login, hostname, HTTPS, interface language), Publication, Schedule, Filters, Translation, LLM, Reader, Notifications (ntfy, when allowed), Categories, Catalog approvals, Users, Backup/Restore, Update, and About |

<p align="center">
  <img src="docs/screenshots/briefing.png?v=0.0.0.8" alt="Briefing" width="280" />
  <img src="docs/screenshots/saved.png?v=0.0.0.8" alt="Saved" width="280" />
  <img src="docs/screenshots/search.png?v=0.0.0.8" alt="Search" width="280" />
  <img src="docs/screenshots/send.png?v=0.0.0.8" alt="Send" width="280" />
  <img src="docs/screenshots/feeds.png?v=0.0.0.8" alt="Feeds" width="280" />
  <img src="docs/screenshots/catalog.png?v=0.0.0.8" alt="Catalog" width="280" />
  <img src="docs/screenshots/status.png?v=0.0.0.8" alt="Status" width="280" />
  <img src="docs/screenshots/settings.png?v=0.0.0.8" alt="Settings" width="280" />
</p>

Refresh in the header fetches every enabled source now. A background tick every minute only fetches sources that are due. Sources set to Global follow the Settings interval; Custom sources keep their own.

## Household accounts

Admin creates people under **Settings → Users**. Each person gets their own feeds, briefing paper, Send files, and OPDS catalog. Non-admins only see Settings tabs that apply to them (no LLM, Schedule, Catalog packages, Users, Backup, or Update).

- **Catalog approvals** — admin ticks which catalog sources household members may Add (“Approved for user viewing”).
- **Permissions** — allow custom feed URLs and/or ntfy (`can_add_custom_sources`, `can_use_ntfy`; both off by default for new users).
- **Login QR** — one-time token from the Users card; open `/login/token/...` or scan the QR, then Hide when done.
- **Interface language** — each user can override the instance default under General (`ui_lang`); Translation’s target language for story text is separate.

On Status, copy **your** catalog URL (`/opds/u/<username>`), not the shared `/opds` admin catalog, when setting up a reader for that person.

## Windows development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Double-click `run-local.bat` (or run it from a prompt). It stops anything already listening on the NewsCast port, then starts the server. Open http://127.0.0.1:8080 and sign in with `admin` / `admin`. Port 8000 is often taken on Windows (for example uniFLOW SmartClient), so NewsCast defaults to 8080.

## Raspberry Pi OS

Step-by-step for a fresh Pi (USB copy, no technical background): see **[INSTALL.md](INSTALL.md)**.

You do not need the whole repo (no `.venv`, tests, or Windows data). On the PC, double-click `deploy\export-pi.bat`. It writes a slim folder at `dist\NewsCast-pi`. Copy that onto the Pi (USB, SCP, or a shared drive), then:

```bash
cd /path/to/NewsCast-pi
sudo ./deploy/install.sh --hostname newscast
```

The script installs system packages (including Avahi for `.local` names), a Python venv, and a systemd service at `/opt/newscast`. `--hostname` sets the Pi’s name so you can open `http://newscast.local:8080` as well as the LAN IP. Omit the flag to keep the current hostname.

It writes a first-run `.env` with `PUBLIC_BASE_URL` on port **8080**. Run the same command again after copying a newer tree to update; an existing `.env` and `data/` folder are left alone. After you publish GitHub Releases, Settings can check and install that update in place (it backs up data first).

Open `http://newscast.local:8080` (or `http://<pi-ip>:8080`), sign in with `admin` / `admin`, then change the password on Settings. You can also change the hostname later on the Settings page (`DEVICE_HOSTNAME` in `.env`).

To rename from the shell:

```bash
sudo /opt/newscast/deploy/set-hostname.sh living-room
```

```bash
sudo systemctl status newscast
```

## E-reader

The reader talks to **one NewsCast at a time**. If you run a copy on the home Pi and another on a work laptop, add each as its own catalog (or point Sync at that machine’s URL). Set an **Instance name** on Settings (Home, Work) so the briefing title shows which copy you pulled.

On Settings → Reader, pick **Xteink** or **Kobo**. Both use OPDS for today’s frozen EPUB and Send-tab files. Use `/opds` for the admin catalog, or `/opds/u/<username>` for a household member. Push is different: CrossPoint has HTTP File Transfer; KOReader does not, so NewsCast uses SSH/SFTP instead.

### Xteink (CrossPoint)

Xteink needs **CrossPoint**. On CrossPoint: Settings → System → OPDS Servers → add `http://<this-copy>:8080/opds` (admin catalog) or `http://<this-copy>:8080/opds/u/<username>` for a household member. Download today’s or yesterday’s **frozen** briefing EPUB (written at the publish time on Settings → Schedule); CrossPoint caches it for offline. Until today’s paper exists, today’s link falls back to yesterday. The catalog also lists files you queued on the Send tab, and category papers when those are enabled under Publication.

To push files while File Transfer is on, set the reader host on Settings → Reader (default `crosspoint.local`) and use **Push now** or **Queue for later** on Status or Send. If the reader is asleep, queued files wait and go when Wi-Fi is back (or on the next minute tick if “Push when the reader is on Wi-Fi” is on).

Leave username and password blank unless you turn on catalog login in NewsCast Settings — asking CrossPoint to log in can crash it.

If catalog login is on, set a catalog username and password on Settings. CrossPoint sends those as HTTP Basic. The same password also works as `Authorization: Bearer <token>` or `?token=` on `/api/x3`.

### Kobo (KOReader)

Kobo needs **KOReader** (stock Nickel has no OPDS). In KOReader: File browser → magnifying glass → OPDS catalog → add `http://<this-copy>:8080/opds` or `http://<this-copy>:8080/opds/u/<username>`. Download the frozen briefing EPUB (standard EPUB, not KEPUB). Catalog login uses HTTP Basic if you turn it on; KOReader supports that.

To push files the way CrossPoint File Transfer works, start KOReader’s **SSH server** (Tools → Network → SSH server, default port **2222**, user **root**) while the Kobo is on Wi-Fi. Set the Kobo’s LAN IP as Reader host and the upload folder to `/mnt/onboard/News` (KOReader’s file browser root is `/mnt/onboard`). **Check reader** probes that SSH port. **Push now** / **Queue for later** copy queued EPUBs and PDFs over SFTP. If SSH is off or the Kobo is asleep, they wait.

| Path | What it serves |
| --- | --- |
| `GET /opds` | Admin / legacy navigation catalog (today’s briefing + Send library) |
| `GET /opds/u/<username>` | That user’s personal OPDS catalog |
| `GET /opds/briefing` | Acquisition feed for Daily Briefings (today and yesterday when published) |
| `GET /opds/library` | Acquisition feed for queued EPUB and PDF files |
| `GET /api/x3/news` | JSON briefing |
| `GET /api/x3/u/<username>/news` | That user’s JSON briefing |
| `GET /api/x3/news.txt` | Plain-text briefing |
| `GET /api/x3/news.epub` | EPUB briefing |

### Xteink Sync (optional)

Stock Xteink firmware talks to Xteink Cloud (`8.130.157.48:8000`). To make Sync pull from this copy:

1. Point the device API host at NewsCast (`http://<pi-ip>:8080`, `http://newscast.local:8080`, or your laptop’s LAN address) using a community firmware patch such as [xteink-sync](https://github.com/Wferr/xteink-sync).
2. Press Sync on the reader. It polls `GET /api/v1/device/tasks` and downloads the latest briefing (`.txt` or `.epub`), plus any EPUB or PDF you queued from the Send tab. Those uploads are not summarised.

This repo does not flash or patch reader firmware.

## Notifications (ntfy)

Optional phone alerts live under **Settings → Notifications**. Configure server, topic, and token, then choose events (paper published and/or reached the reader). Off by default.

Admins always see the tab. Other users need **Allow ntfy** on their People card; blank server falls back to the household default while each person can keep their own topic and token.

## Configuration

Environment variables in `.env` are deploy-time defaults. Settings can override admin credentials, hostname, instance name, refresh interval, the summary provider (OpenAI or Ollama), and the reader catalog login. UI values live in SQLite and win over `.env` until cleared.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOST` / `PORT` | `0.0.0.0` / `8080` | Bind address. Use 8080 on Windows so another app is less likely to block start. In-app HTTPS uses this same port |
| `DATABASE_URL` | `sqlite:///./data/newscast.db` | SQLite path |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | `admin` / `admin` | Web UI login (hashed at rest after first run) |
| `SESSION_SECRET` | generated into `data/session.secret` | Signs the login cookie (stdlib HMAC) |
| `OPENAI_API_KEY` | empty | Concise AI summaries when the provider is OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI chat model |
| `LLM_PROVIDER` | `openai` | `openai` or `ollama` |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Local Ollama server |
| `OLLAMA_MODEL` | empty | Ollama model id, for example `llama3.2` |
| `INSTANCE_NAME` | empty | Label on the reader briefing (Home, Work) |
| `INGEST_INTERVAL_MINUTES` | `60` | Default global refresh interval (overridable on Settings; each feed can use Global or Custom) |
| `MAX_STORIES_PER_FEED` | `8` | Cap on new stories taken from one source per fetch |
| `STORY_RETENTION_DAYS` | `7` | Unfavourited stories older than this are removed |
| `SEED_RECOMMENDED_FEEDS` | `true` | First-run catalog |
| `X3_SYNC_TOKEN` | empty | Catalog password / optional lock on `/opds` and `/api/x3/*` |
| `X3_CATALOG_LOGIN` | empty | Set to `1` to require catalog username and password (off by default) |
| `X3_CATALOG_USERNAME` | `newscast` | Username CrossPoint sends when catalog login is on |
| `X3_DEVICE_ID` | empty | Device id for Sync tasks |
| `X3_BRIEFING_FORMAT` | `txt` | `txt` or `epub` for the Sync briefing file |
| `X3_SAVE_PATH` | `/Pushed Files/NewsCast/` | Folder the Sync firmware writes into on the reader |
| `PUBLIC_BASE_URL` | `http://127.0.0.1:8080` | Catalog and sync file URLs when no hostname is set. Localhost falls back to the detected LAN IP; becomes `https://…` when Use HTTPS is on |
| `DEVICE_HOSTNAME` | empty | Pi / `.local` name; also editable on Settings |
| `GITHUB_REPO` | empty | `owner/NewsCast` for in-app GitHub Release checks; also set on Settings |

The LAN UI is HTTP by default. Turn on **Use HTTPS on the LAN** under Settings → General to generate a local CA and serve TLS on the same port (see [INSTALL.md](INSTALL.md)). Download the root CA and trust it once on each phone or PC. Treat the OpenAI key and backup zips as only as safe as your local network.

## Tests

```powershell
.\.venv\Scripts\python -m pytest -q
```
