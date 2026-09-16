# Changelog

Current version is **0.0.0.9**. New work is appended under that version until you ask to bump it.

## 0.0.0.9 — 2026-09-16

### Added
- In-app LAN HTTPS: Settings → General → Use HTTPS on the LAN creates a household local CA and server certificate (`app/services/tls.py`, `cryptography`) and serves TLS on the existing app port via `python -m app.serve`
- Admin **Download root CA** (`/settings/tls/root-ca.pem`) plus certificate status (expiry, SANs) on General
- **Restart to enable HTTPS** on General after the CA is ready (HTTPS stays on HTTP until you restart, so the CA can be downloaded first)
- Backup/restore includes `data/tls/` so the same CA survives restores

### Fixed
- Enabling HTTPS no longer auto-restarts before you can download the root CA (avoids “site can’t be reached” when the browser is still on `http://`)

### Changed
- Share/OPDS https URLs include the app port (e.g. `https://newscast.local:8080`) instead of implying port 443
- `deploy/run.sh` and `run-local.bat` start through `app.serve` (HTTP or HTTPS, never both)
- Turning HTTPS **off** still schedules a restart back to plain HTTP; turning it **on** waits for Download root CA + Restart to enable HTTPS
- Hostname changes while HTTPS is on renew the certificate and prompt a restart when needed
- README / INSTALL document in-app HTTPS and CA trust steps; `deploy/Caddyfile` demoted to optional/advanced
- Settings action buttons gain small icons (Download root CA, Restart HTTPS, Catalog Import/Export, Select/Unselect all, Save approvals, Load from Ollama, Save user, Create account, Add category, Download/Restore backup, Roll back, Check for updates)
- People card button label is **Save user** (username removed from the label)
- Transfer queue **display** order: today’s paper first, then older papers (newest date first), then Send files (push still runs oldest-first)

## 0.0.0.8 — 2026-09-15

### Added
- Opt-in LAN HTTPS under Settings → General (reverse proxy); Secure cookies and https share/OPDS URLs when enabled
- Always-on argon2id password hashing and login rate limiting
- Household multi-user accounts with per-user feeds, papers, library, OPDS, ntfy, and shared URL fetch cache
- Interface language under Settings → General (`app/services/i18n.py`, `app/locales/*.json`); nav, login, ingest pill, and JS busy/confirm strings via `t()`; Spanish scaffold for fallbacks
- Per-user ntfy topic/token/events (blank server uses household default); Settings → Notifications writes user settings
- Admin permission `can_use_ntfy` (off by default) gates the Notifications tab and ntfy sends for non-admins
- Per-user OPDS/X3 mounts at `/opds/u/{username}` and `/api/x3/u/{username}`; legacy `/opds` and `/api/x3` keep serving the admin catalog
- Catalog approvals (Settings → Catalog) so non-admins only Add household-approved sources; custom URL add gated by `can_add_custom_sources`
- One-time login QR / `/login/token/{token}` from Settings → Users

### Changed
- Backup/restore covers nested library paths, briefings, and feed cache used by multi-user installs
- UI `ui_lang` prefers the signed-in user’s setting over the instance General default
- Backup download/restore stays admin-only
- Non-admins only see their Settings tabs (no LLM, Schedule, Catalog packages, Users, Backup, Update)
- Status emphasizes each user’s `/opds/u/{username}` catalog URL
- Catalog approvals titled “Approved for user viewing”, with Select all / Unselect all
- Startup migrates `users.can_use_ntfy` before loading the admin user (fixes local DB upgrade crash)
- Settings → Users is a polished People list: per-user cards with permission chips, editable custom feeds / ntfy / active, password reset, login QR, and remove account
- Status catalog/share URLs wrap on narrow phones so long `/opds/u/…` links no longer overflow the gutter
- README phone screenshots refreshed for 0.0.0.8; Web UI table and OPDS paths updated for multi-user / Settings tabs
- README and INSTALL brought in line with current capability: household accounts, ntfy, HTTPS/Caddy, Publication/Translation, per-user OPDS, and argon2id login notes
- Admin can remove a household user (and that user’s feeds, stories, papers, and Send files); admin accounts stay protected
- Show login QR / create-user forms submit as normal page posts so the QR card actually appears (fetch+reload was dropping it)
- Login QR card has a Hide button to dismiss it after scanning
- About shows the GitHub profile link instead of the author name
- Status / Send queue rows stack on phones so paper titles are not crushed by “Remove from transfer”
- Action buttons gain small icons (Save article, Search, Check reader, Push now, Queue for later, Remove, Add file, Save settings)
- Send “Your files” rows also stack on narrow screens
- Mobile buttons are more compact (shorter labels, denser action rows, slightly smaller tap height)

## 0.0.0.7 — 2026-09-15

### Added
- Settings Notifications tab for optional ntfy phone alerts when today’s paper is published and/or reaches the reader (off by default; configure server, topic, and token first)
- Settings Translation tab sets a global target language (default English) for Translate feeds; Google and LLM follow that language, and stories store `content_lang` for backfill when the target changes

### Changed
- Favicon startup reconciles icons already on disk and only network-fetches for enabled feeds (and Saved) that are truly missing, with a cooldown after failures
- Send tab splits waiting transfers from Your files, labels today’s paper by briefing path (not custom save name), and aligns Status push copy

## 0.0.0.6 — 2026-09-15

### Added
- Settings Translation tab sets the global Translate to English engine: Google Translate (Chrome fallback) or the configured LLM
- Each feed can choose As published, follow the global setting, force Google, or force LLM
- Category mix can allocate Briefing and daily-paper slots by percentage (blank categories share the leftover; 0 excludes)
- Optional per-category OPDS papers: tick topics to publish separate EPUBs listed under OPDS → Categories
- Settings Publication tab (before Schedule) for paper composition settings
- Category papers use their own title pattern (with `{category}`) instead of suffixing the full-paper name

### Changed
- Briefing reloads automatically when a full refresh finishes, so new and translated stories appear without a manual reload
- Settings Translation tab stays visible (it was incorrectly hidden by the client tab switcher)
- Success toasts appear at the top-right on desktop so they stay clear of settings tabs and Save settings
- Catalog cards keep category/type chips under the title beside Add/Remove, so the button no longer pushes them down
- Paper naming moves from Reader to Publication; Briefing size and importance move from Schedule to Publication
- Schedule keeps refresh timing and Publish at only
- OPDS acquisition links put day, category, and the paper filename in the path so readers save category papers under the displayed name (not `news.epub`)
- Category EPUB metadata (`dc:title`) uses the category title pattern; changing naming patterns needs a republish for already-written papers
- Settings Device tab is labelled General (URL `?tab=device` unchanged)

## 0.0.0.5 — 2026-09-14

### Added
- Stories get an importance score from 1–5 (LLM when configured, heuristics otherwise); Settings Schedule can keep only stories at or above a threshold (default 3+)
- Status Health card shows disk free space, database size, failing feeds with error details, and the last AI error
- Status Delivery card shows last published time, last successful push, queue age, and a trust line such as "Morning paper is on the reader"

### Changed
- Daily EPUB/TXT papers open with a masthead of story counts by category and denser cover digest blurbs
- Settings Schedule and Reader Sync show a short delivery snapshot (published / last push / queue)

## 0.0.0.4 — 2026-09-14

### Added
- Settings Reader can set the paper title/filename pattern and date style used by OPDS, EPUB metadata, and downloads (`{product}`, `{hostname}`, `{instance}`, `{label}`, `{date}`)
- Settings Reader can set a custom paper label for `{label}`; Device instance name is `{instance}`
- Settings Schedule can limit Global refresh to local active hours (for example every hour from 09:00 until 12:00)

### Changed
- Settings Reader splits connection, Sync, and Paper naming into separate cards
- Settings Reader Paper naming offers clickable `{token}` chips and a live filename preview while editing
- Settings Catalog holds package import and category export; the Catalog page is browse and add/remove only
- Saved marks long-reads as From Briefing or Added URL; Briefing notes that bookmarked stories live on the Saved tab

## 0.0.0.3 — 2026-09-14

### Added
- Settings Reader can target Xteink (CrossPoint HTTP push) or Kobo (KOReader OPDS plus SSH/SFTP push)
- Kobo Check reader probes the KOReader SSH port; Push now and Queue for later copy queued EPUBs and PDFs over SFTP into `/mnt/onboard/News`
- Status and README document KOReader OPDS setup and SSH server steps beside the existing CrossPoint path
- Daily briefing EPUB includes a generated newspaper-style cover image for library previews

### Changed
- Reader host, upload folder, and push hints follow the chosen device (CrossPoint File Transfer vs KOReader SSH)
- OPDS navigation uses **Daily Briefings** (today and yesterday, newest first) beside Library; only published papers appear
- Reader EPUB contents and TOC nest Category → Source → stories, with more space between entries for e-ink reading
- Feeds cards keep the source header visible and collapse schedule and filters behind Schedule & filters by default
- Catalog shows Remove on added sources (including custom ones), so feeds can be taken off without opening Feeds
- Feeds Schedule & filters uses a compact toggle with a fixed-size chevron; Catalog Remove no longer 404s after confirm
- Feeds Schedule & filters expands with native details (no JS), and static CSS/JS cache-bust when assets change
- Feeds, Catalog, and Briefing keep the active chip filter after enable, add, remove, and other reloads
- Feeds and Catalog tighten for narrow phones: stacked headers, single-column catalog, and package forms that stay inside the page margins

### Fixed
- Status OPDS and catalog links use the LAN IP when `PUBLIC_BASE_URL` is still localhost, so readers are not pointed at 127.0.0.1
- Daily briefing downloads use dated filenames, and new papers stamp the local paper date in the EPUB so the cover matches the calendar day
- Story summaries drop AI preambles such as “Here is a concise news briefing based on the provided text”

## 0.0.0.2 — 2026-09-13

### Added
- Reader newspaper publishes at a set local time (default 06:30) as a dated EPUB with category TOC, e-ink CSS, and a cover; Status can publish now
- Status and Send list queued CrossPoint files with briefing vs Send labels and a cancel control
- Show-password icons on the new and confirm password fields on Settings Device
- Colour palettes Default, Ocean, Forest, and Slate, each with the existing light / dark / Auto switch; picker is on Settings Device
- README shows phone-width screenshots: sign-in, then Briefing, Saved, Search, Send, Feeds, Catalog, Status, and Settings
- Briefing chips switch Today and Yesterday; OPDS and `/api/x3/news.epub` can serve yesterday as well
- Briefing bookmark saves a story as a Saved long-read beside the star
- Feeds can mute a source for 24 hours, add include/exclude words, and show a Healthy / Empty / Error badge
- Settings Filters tab holds global include/exclude words; each feed can add more
- Search tab looks through briefing stories, favourites, and Saved long-reads with SQL LIKE
- Status and Send can push the briefing and library to CrossPoint when it is on Wi-Fi, or queue files while the reader is asleep

### Changed
- OPDS and `/api/x3/news.epub` serve the frozen dated paper instead of rebuilding from live stories
- Refresh no longer queues a new briefing file; the publish job or Publish now does that, and the same reader path is not queued twice
- Settings Access tab is now Device; the hostname card is labelled Network
- Search uses the same panel head, form layout, and text-field styling as Saved and Send
- Send and Status no longer ping the reader on load; use Check reader to see if it is online or asleep
- Sign-in follows the saved palette and light / dark mode but no longer has switches to change them

### Fixed
- Installing a GitHub update on Windows no longer deletes the running `app` folder, which caused an internal server error after the backup
- Status and Settings treat a GitHub release that matches this copy as up to date, so they no longer offer to install the version already running

## 0.0.0.1 — 2026-09-13

### Added
- FastAPI + SQLite news aggregator with env-based config for Windows and Raspberry Pi OS
- Mobile-first UI: Briefing, Feeds, Catalog, Status
- Recommended RSS catalog with first-run seeding
- Custom source form for RSS or website URLs
- Website scraping when a source has no usable RSS (discover feed links, then scrape article pages)
- Preconfigured additional sources: Hackaday, Krebs on Security, iTnews
- Deduped story ingest and concise summaries (OpenAI when a key is set)
- Xteink X3 JSON, TXT, EPUB, and sync task endpoints
- Admin login (`admin` / `admin`) and Status-panel settings for credentials, OpenAI, and X3 tokens
- Tab icons next to Briefing, Feeds, Catalog, and Status labels
- This changelog and app version `0.0.0.1`
- OpenAI model picker on Status (gpt-4o-mini, gpt-4o, gpt-4.1-mini, gpt-4.1, o4-mini, or a custom id)
- Global refresh interval on Status, plus per-source Global vs Custom schedule on Feeds
- Background ingest ticks every minute and only fetches sources that are due; Refresh still fetches all enabled sources
- Light / Dark / Auto theme switch in the header, persisted locally
- UI polish: paper wash, story accent rule, status chip, focus rings, and tighter header/nav chrome
- Default local port changed to 8080 so Windows installs are not blocked by another app on 8000
- In-page sign-in (styled login page) instead of the browser HTTP Basic popup
- Category chips on Feeds and Briefing, matching Catalog
- Refresh button icon in the header, plus a sign-out control
- Status upload for EPUB or PDF files that skip summarising and queue a direct X3 sync task
- `run-local.bat` for Windows: stop any process on the NewsCast port, then start the server
- Catalog Add / Added controls use matching full-width buttons with plus and check icons
- Status settings headings (Admin access, Refresh schedule, API access) sit inside their cards
- Line-drawn satellite mark to the left of the NewsCast name in the header and on sign-in
- Brand mark changed to a small display with a newspaper inside
- Status Send a file panel and stat cards: tighter heading, even padding, and a cleaner file picker
- Publication date on each briefing story (UI, TXT, and EPUB)
- Briefing date now reads published_at/created_at in the template so it always shows beside the source
- Stories expire after 7 days unless favourited; star on each briefing card keeps them
- Per-source Summarise / Full article setting on Feeds; full mode stores the extracted article instead of a short summary
- Settings tab for admin, schedule, and API forms; Status keeps health, file send, and X3 links
- Feeds cards: header with On/Off and trash icon, inset schedule tray, plus-icon Add custom button
- Catalog cards: compact Add/Added pills in the header, accent rule, tighter type
- Catalog titles sit in a fixed two-line slot so every card lines up
- Sticky category chips sit below the header instead of on its border when you scroll
- Sticky filter chips sit just under the header line, without an extra floating gap
- `deploy/install.sh` for a fresh Raspberry Pi OS copy: packages, venv, systemd, LAN `.env`
- Install `--hostname` and `deploy/set-hostname.sh` so the Pi is reachable as `http://NAME.local:8080`
- `DEVICE_HOSTNAME` in `.env` and a Device hostname field on Settings; X3 and Status URLs use `http://NAME.local`
- `deploy/export-pi.bat` builds a slim `dist/NewsCast-pi` folder to copy onto the Pi
- `INSTALL.md` how-to for a fresh Raspberry Pi OS install, written for a non-technical user
- Install guide covers SSH, Raspberry Pi Connect, and copying the folder from Windows without a USB stick
- Saved tab: paste a URL, scrape the full article, keep 7 days or a custom date, remove by hand; included in the X3 briefing
- Ollama as an optional summary provider next to OpenAI, with URL, model, and Load from Ollama
- Instance name so a home Pi and a work laptop each label their X3 briefing; Status shows this copy’s URL
- Catalog cards show RSS vs Scrape, with type filters and Add custom for your own feed or website
- TechCrunch in the recommended catalog as RSS (`https://techcrunch.com/feed/`)
- OPDS catalog at `/opds` for CrossPoint: today's briefing EPUB and Status library files; HTTP Basic uses the X3 token
- Settings option to require a CrossPoint username and password; off by default because that login can crash the X3
- Catalog username and password fields on Settings when you do want CrossPoint to log in
- Reader-facing labels (Status, Settings, Saved) no longer name a specific Xteink model
- Favicons are fetched once when a feed is added, cached on disk, and shown on Briefing, Saved, Feeds, and Catalog cards
- Favicon capture tries the feed first, then the publication website (BBC RSS falls back to bbc.com)
- Filter chips on Briefing, Feeds, and Catalog show a small icon next to News, Tech, Sport, and the other categories

### Fixed
- Custom-feed sheet overlay blocked taps because `display: grid` overrode the `hidden` attribute
- Catalog filters left a tall blank gap: hidden groups could still take layout, and scroll anchoring kept the old position
- Header and Status stayed on Refreshing during long first fetches; ingest now skips full-article downloads when RSS already has a usable excerpt, and Refresh returns immediately while status polls in the background
- Website scrape sources such as TechCrunch hung on the homepage; ingest now tries `/feed/` and other common RSS paths first, and scraped stories keep a date so they stay in the briefing
- Briefing no longer drops undated scrapes or buries a new source under a single busy feed
- Delete feed and saved-article confirmations use the same in-app sheet as Add custom, instead of the browser popup
- Saved articles now fetch the page as HTML (not RSS), accept URLs without https://, and show an on-page error when the scrape fails
- Save and error toasts are a compact centred chip instead of a full-width black bar, with green or red colour by outcome
- Successful save toasts survive the page reload and stay up longer so they can be read
- TechCrunch and AP favicons were blank: skip empty default .ico files, read the site icon from the homepage, and use a public icon helper when the site blocks us
- Filtering to a single item (Favourites, Security, and similar) left a blank gap above the page heading; the list now jumps back to the top
- Briefing (and Saved) favicons sit beside the source chip instead of inside it, so they line up with the date and star
- Saved articles use the same favicon capture as feeds (page, then helper) and show a cached icon even when the host is not a subscribed source
- Cached source favicons are stored in the app and copied onto a fresh Pi so Catalog and Feeds show icons without re-fetching
- Version number moved from the header and Status onto an About card on Settings, with author and a short description
- Feed On/Off control is a quieter chip labelled Enabled or Disabled
- Schedule lengths of an hour or more show as hours (24 hrs) instead of minutes (1440 min)
- Send tab for EPUB and PDF uploads; Status keeps health and reader links
- README lists the current tabs, CrossPoint OPDS paths, Send library, and Settings overrides
- Status shows a QR code for this copy’s hostname or LAN IP so a phone can open the app
- Status Open on this network sits below Reader, with iOS home-screen steps named NewsCast plus the instance (NewsCast Home)
- Phone layout keeps the tab bar on the bottom and filter chips under the header without a pinch-zoom to correct them
- Catalog two-column cards shrink on a phone so titles and Add fit without overflowing
- Catalog is a browsable library of news, culture, tech, science, and other feeds; Add captures the publication favicon
- Desktop filter chips wrap so every category stays visible and the horizontal scrollbar does not appear
- Sticky filter chips span the full column so briefing cards do not show through on the right when you scroll
- Scrollbars use a thin ink-on-paper thumb so they match the UI when they appear
- Catalog category and type chips keep an even 8px gap when the category row shows a scrollbar
- Desktop page scrollbar uses the same ink-on-paper track and thumb as the rest of the app
- Nordic catalog sources (DR, Politiken, SVT, NRK, and others) translate headline and excerpt to English with the Google Translate web API before they are stored
- Feeds can set Language to Translate to English for any custom source
- Catalog adds Copenhagen sources (DR Copenhagen, TV 2 Kosmopol, The Copenhagen Post, The Local Denmark) and key Australian news, business, and sport feeds
- Catalog filter News is now World News; Australian sources sit under their own Australia chip, like Nordic
- iTnews moves from Tech into the Australia catalog group
- Translation uses a Google POST request and a Chrome fallback when the free endpoint rate-limits, and existing Danish briefing stories are backfilled on refresh
- Chrome translate replies no longer append the language code to the English headline or excerpt
- Feeds cards have an update control that fetches that source only, without refreshing every feed
- Settings can set how many stories Briefing shows: 10, 20, 30, 40, or 50
- Settings can check GitHub Releases, validate a zip, back up data, then install and restart without touching data or .env
- Settings can download or restore a backup of the database, Send library, and .env
- Settings can add custom categories; Catalog can import and export a JSON package of providers
- Settings Backup card lines up Download with the other pills, centres its label, and leaves space above Restore
- Settings uses its own tabs: Access, Schedule, LLM, Reader, Categories, Backup, and About
- Settings tabs are links with icons so you can move between them even if the script is cached
- Settings splits Backup and Update: restore stays on Backup; GitHub check and install sit on Update
- Settings tab is labelled Backup/Restore, and Roll back last app sits there with restore, not on Update
- Git ignore covers SQLite WAL files, Cursor settings, and leftover cookie dumps so they stay off GitHub
