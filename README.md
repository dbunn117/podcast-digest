# Podcast Digest

Daily podcast digest dashboard for David's favorite podcasts and ad hoc podcast links.

Live site: https://dbunn117.github.io/podcast-digest/

## Current app

The hosted dashboard is now a static, interactive **David Podcast OS** app. It pulls RSS-visible 2026 YTD history for:

- All-In
- The Grade Cricketer
- The Startup Ideas Podcast
- This Week in Startups
- Prof G Markets
- Modern Wisdom
- The Game with Alex Hormozi
- The Diary Of A CEO

## Features

- Health OS-inspired light dashboard layout with a fixed command-center sidebar, warm cards, gradient show tiles, filters, and responsive grids
- 2026 YTD episode history
- Search by title, show notes, podcast, and theme
- Podcast and theme filters
- Newest/oldest/podcast sorting
- Full episode links where feeds expose a page URL
- Direct audio links where feeds expose enclosure URLs
- Podcast artwork / episode artwork where RSS exposes it
- Monthly episode history and podcast mix charts
- Tag/theme cloud aligned to David's interests
- Obsidian daily digest output

## Running it

Don't call `podcast_digest.py` directly with `--write` — that flag makes it write its own unenhanced copy straight to Obsidian, which caused a real duplicate-write bug (2026-09-08). The daily job is `/root/.hermes/profiles/personal/scripts/podcast_digest_collect.py`, which calls this script *without* `--write`, then writes the single Obsidian copy itself deterministically, then sweeps digests older than 30 days into `Daily Digests/Archive/YYYY-MM/`. Run that wrapper, not this script directly:

```bash
python3 /root/.hermes/profiles/personal/scripts/podcast_digest_collect.py
```

## Outputs

- Hosted dashboard source: `/root/podcast-digest/docs/index.html`
- Hosted dashboard data: `/root/podcast-digest/docs/episodes.json`
- Raw episode data: `/root/podcast-digest/data/episodes.json`
- Latest digest markdown: `/root/podcast-digest/data/latest_digest.md`
- Obsidian daily digests: `/root/obsidian/David OS/02 Personal/Media/Podcasts/Daily Digests/` (rolling 30-day window; older digests archive automatically to `Archive/YYYY-MM/`)
- Obsidian link inbox: `/root/obsidian/David OS/02 Personal/Media/Podcasts/Podcast Link Inbox.md`

## Next improvements

- Add transcript extraction for YouTube/Spotify/Apple links where available.
- Add per-episode AI-generated takeaways against David's business/health/content lenses.
- Add “save this podcast link” Telegram workflow that appends to the inbox.
- Promote strong takeaways into content ideas, business ideas, people follow-ups, or health experiments.
