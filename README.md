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
- The Diary Of A CEO

## Features

- Modern SaaS-style dark dashboard layout with glass panels, gradient show cards, sticky filters, and responsive grids
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

## Commands

```bash
python3 /root/podcast-digest/podcast_digest.py --days 7 --since 2026-01-01 --write
```

## Outputs

- Hosted dashboard source: `/root/podcast-digest/docs/index.html`
- Hosted dashboard data: `/root/podcast-digest/docs/episodes.json`
- Raw episode data: `/root/podcast-digest/data/episodes.json`
- Latest digest markdown: `/root/podcast-digest/data/latest_digest.md`
- Obsidian daily digests: `/root/obsidian/David OS/08 Podcasts/Daily Digests/`

## Next improvements

- Add transcript extraction for YouTube/Spotify/Apple links where available.
- Add per-episode AI-generated takeaways against David's business/health/content lenses.
- Add “save this podcast link” Telegram workflow that appends to the inbox.
- Promote strong takeaways into content ideas, business ideas, people follow-ups, or health experiments.
