# Changelog

Current version is **0.0.0.2**. New work is appended under that version until you ask to bump it.

## 0.0.0.2 — 2026-09-13

### Added
- Briefing chips switch Today and Yesterday; OPDS and `/api/x3/news.epub` can serve yesterday as well
- Briefing bookmark saves a story as a Saved long-read beside the star
- Feeds can mute a source for 24 hours, add include/exclude words, and show a Healthy / Empty / Error badge
- Settings Filters tab holds global include/exclude words; each feed can add more
- Search tab looks through briefing stories, favourites, and Saved long-reads with SQL LIKE
- Status and Send can push the briefing and library to CrossPoint when it is on Wi-Fi, or queue files while the reader is asleep

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
