#!/usr/bin/env python3
"""Podcast Digest for David.

Fetches favorite podcast RSS feeds, tracks YTD history, reads a link inbox,
and writes:
- /root/podcast-digest/data/episodes.json
- /root/podcast-digest/data/latest_digest.md
- /root/podcast-digest/docs/index.html
- /root/podcast-digest/docs/episodes.json
- Obsidian daily digest note under 08 Podcasts/Daily Digests/

The dashboard is a static modern SaaS-style app: all interactivity is client-side
from docs/episodes.json so it works on GitHub Pages without a backend.
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import re
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/root/podcast-digest')
CONFIG = ROOT / 'config.json'
DATA = ROOT / 'data'
DOCS = ROOT / 'docs'
PACIFIC = ZoneInfo('America/Los_Angeles')

KEYWORDS = {
    'AI': ['ai', 'artificial intelligence', 'llm', 'openai', 'anthropic', 'claude', 'gpt', 'agent', 'automation', 'machine learning', 'chatgpt'],
    'Startups': ['startup', 'founder', 'venture', 'vc', 'bootstrapped', 'saas', 'business idea', 'product market', 'mvp'],
    'Business': ['business', 'revenue', 'sales', 'market', 'operator', 'strategy', 'pricing', 'distribution', 'profit'],
    'Finance': ['finance', 'accounting', 'private equity', 'investment', 'markets', 'rates', 'valuation', 'capital'],
    'Career': ['career', 'job', 'work', 'leadership', 'network', 'linkedin', 'personal brand'],
    'Health': ['health', 'sleep', 'fitness', 'diet', 'metabolism', 'stress', 'exercise', 'longevity', 'glucose', 'diabetes'],
    'Parenting': ['parent', 'parenting', 'kids', 'children', 'family', 'relationship'],
    'Content': ['content', 'creator', 'storytelling', 'audience', 'newsletter', 'writing'],
    'Cricket': ['cricket', 'test', 'odi', 't20', 'world cup', 'ashes', 'south africa', 'proteas', 'ipl', 'mlc'],
    'Sport': ['sport', 'sports', 'rugby', 'tennis', 'golf', 'nba', 'football', 'soccer'],
}

PODCAST_ACCENTS = {
    'All-In': {'accent': '#6d5dfc', 'gradient': ['#1f2937', '#4f46e5'], 'emoji': '♟️'},
    'Grade Cricketer': {'accent': '#16a34a', 'gradient': ['#064e3b', '#22c55e'], 'emoji': '🏏'},
    'Startup Ideas': {'accent': '#f97316', 'gradient': ['#7c2d12', '#fb923c'], 'emoji': '🚀'},
    'DOAC': {'accent': '#dc2626', 'gradient': ['#450a0a', '#ef4444'], 'emoji': '🎙️'},
    'Link inbox': {'accent': '#0891b2', 'gradient': ['#164e63', '#06b6d4'], 'emoji': '🔗'},
}


def strip_html(s: str) -> str:
    s = re.sub(r'<(script|style).*?</\1>', ' ', s or '', flags=re.S | re.I)
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'</p\s*>', '\n', s, flags=re.I)
    s = re.sub(r'<li\b[^>]*>', '\n• ', s, flags=re.I)
    s = re.sub(r'<.*?>', ' ', s)
    s = html.unescape(s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def fetch_url(url: str, timeout=35) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'ScoutPodcastDigest/2.0 (+https://github.com/dbunn117/podcast-digest)',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        },
    )
    return urllib.request.urlopen(req, timeout=timeout).read()


def parse_date(s: str | None):
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace('Z', '+00:00')).astimezone(timezone.utc)
    except Exception:
        return None


def tag_text(title: str, notes: str, feed_themes=None) -> list[str]:
    text = (title + ' ' + notes).lower()
    tags = []
    for tag, words in KEYWORDS.items():
        for w in words:
            if re.search(r'(?<![A-Za-z0-9])' + re.escape(w.lower()) + r'(?![A-Za-z0-9])', text):
                tags.append(tag)
                break
    seen_lower = {t.lower() for t in tags}
    for theme in feed_themes or []:
        theme = str(theme).strip()
        label = theme.upper() if theme.lower() == 'ai' else theme.title()
        if label and label.lower() not in seen_lower and len(tags) < 7:
            tags.append(label)
            seen_lower.add(label.lower())
    return tags[:8]


def short_summary(text: str, max_chars=620) -> str:
    text = strip_html(text)
    if not text:
        return ''
    # Drop common subscription boilerplate.
    text = re.sub(r'(?i)subscribe.*?(apple podcasts|spotify|youtube).*?\.', ' ', text)
    text = re.sub(r'(?i)follow (us|the show).*?\.', ' ', text)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    picked = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(' '.join(picked)) > max_chars:
            break
        if 35 <= len(s) <= 320 and not re.search(r'(?i)promo code|sponsor|advertis', s):
            picked.append(s)
        if len(picked) >= 3:
            break
    out = ' '.join(picked) or text[:max_chars]
    return out[:max_chars].rstrip()


def find_child_text(node, names: list[str]) -> str:
    for name in names:
        val = node.findtext(name)
        if val:
            return val.strip()
    return ''


def find_itunes_image(node) -> str:
    for el in node.iter():
        if el.tag.endswith('}image') or el.tag == 'itunes:image':
            href = el.attrib.get('href') or el.attrib.get('url')
            if href:
                return href.strip()
    return ''


def parse_feed(feed: dict) -> tuple[list[dict], dict]:
    raw = fetch_url(feed['feed_url'])
    root = ET.fromstring(raw)
    channel = root.find('channel')
    items = channel.findall('item') if channel is not None else root.findall('.//item')
    podcast_image = find_itunes_image(channel or root)
    if not podcast_image and channel is not None:
        podcast_image = channel.findtext('image/url') or ''
    podcast_meta = {
        'name': feed['name'],
        'short_name': feed.get('short_name') or feed['name'],
        'feed_url': feed['feed_url'],
        'themes': feed.get('themes') or [],
        'image': podcast_image,
        **PODCAST_ACCENTS.get(feed.get('short_name') or feed['name'], {}),
    }
    episodes = []
    for item in items:
        title = find_child_text(item, ['title'])
        link = find_child_text(item, ['link'])
        guid = find_child_text(item, ['guid']) or link or title
        pub = parse_date(find_child_text(item, ['pubDate', 'published', 'updated']))
        desc = item.findtext('description') or item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or ''
        duration = item.findtext('{http://www.itunes.com/dtds/podcast-1.0.dtd}duration') or ''
        episode_image = find_itunes_image(item) or podcast_image
        enclosure = ''
        enc = item.find('enclosure')
        if enc is not None:
            enclosure = enc.attrib.get('url', '')
        clean = strip_html(desc)
        stable = guid or link or f"{feed['name']}::{title}::{pub.isoformat() if pub else ''}"
        episodes.append({
            'id': hashlib.sha1(stable.encode('utf-8', 'ignore')).hexdigest()[:16],
            'podcast': feed['name'],
            'short_name': feed.get('short_name') or feed['name'],
            'podcast_image': podcast_image,
            'image': episode_image,
            'accent': PODCAST_ACCENTS.get(feed.get('short_name') or feed['name'], {}).get('accent', '#4f46e5'),
            'emoji': PODCAST_ACCENTS.get(feed.get('short_name') or feed['name'], {}).get('emoji', '🎧'),
            'title': title,
            'url': link,
            'audio_url': enclosure,
            'guid': guid,
            'published_at': pub.isoformat() if pub else None,
            'published_date': pub.astimezone(PACIFIC).date().isoformat() if pub else None,
            'published_month': pub.astimezone(PACIFIC).strftime('%Y-%m') if pub else None,
            'duration': duration,
            'summary': short_summary(clean),
            'show_notes': clean[:5000],
            'tags': tag_text(title, clean, feed.get('themes')),
            'source': 'favorite_feed',
        })
    return episodes, podcast_meta


def read_link_inbox(path: Path) -> list[dict]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Podcast Link Inbox\n\nSend podcast links to Scout and they can be added here for summarization.\n\n## Links\n\n', encoding='utf-8')
    txt = path.read_text(encoding='utf-8')
    urls = re.findall(r'https?://[^\s)\]>]+', txt)
    out = []
    for url in urls:
        out.append({
            'id': hashlib.sha1(url.encode()).hexdigest()[:16],
            'podcast': 'User-sent link',
            'short_name': 'Link inbox',
            'podcast_image': '',
            'image': '',
            'accent': PODCAST_ACCENTS['Link inbox']['accent'],
            'emoji': PODCAST_ACCENTS['Link inbox']['emoji'],
            'title': url,
            'url': url,
            'audio_url': '',
            'guid': url,
            'published_at': None,
            'published_date': None,
            'published_month': None,
            'duration': '',
            'summary': 'Podcast/audio link sent by David. Needs source-specific transcript or show-note extraction.',
            'show_notes': '',
            'tags': tag_text(url, ''),
            'source': 'link_inbox',
        })
    return out


def episode_sort_key(e):
    return e.get('published_at') or ''


def load_config():
    return json.loads(CONFIG.read_text())


def filter_since(episodes: list[dict], since: date | None) -> list[dict]:
    if since is None:
        return episodes
    out = []
    for e in episodes:
        pd = e.get('published_date')
        if not pd:
            if e.get('source') == 'link_inbox':
                out.append(e)
            continue
        try:
            if date.fromisoformat(pd) >= since:
                out.append(e)
        except Exception:
            pass
    return out


def build_stats(episodes: list[dict], ytd: list[dict], recent: list[dict], podcasts: list[dict]) -> dict:
    by_podcast = Counter(e['short_name'] for e in ytd)
    by_tag = Counter(t for e in ytd for t in e.get('tags', []))
    by_month = Counter(e['published_month'] for e in ytd if e.get('published_month'))
    latest_by_podcast = {}
    for e in sorted(episodes, key=episode_sort_key, reverse=True):
        latest_by_podcast.setdefault(e['short_name'], e)
    return {
        'total_available': len([e for e in episodes if e.get('source') == 'favorite_feed']),
        'ytd_count': len([e for e in ytd if e.get('source') == 'favorite_feed']),
        'recent_count': len(recent),
        'podcast_count': len(podcasts),
        'link_inbox_count': len([e for e in episodes if e.get('source') == 'link_inbox']),
        'by_podcast': dict(by_podcast),
        'by_tag': dict(by_tag.most_common(18)),
        'by_month': dict(sorted(by_month.items())),
        'latest_by_podcast': latest_by_podcast,
    }


def build_digest(episodes, days: int, config):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    recent = []
    for e in episodes:
        pub = parse_date(e.get('published_at')) if e.get('published_at') else None
        if e.get('source') == 'link_inbox' or (pub and pub >= cutoff):
            recent.append(e)
    recent.sort(key=episode_sort_key, reverse=True)
    lines = []
    lines.append(f"# Podcast Digest - {datetime.now(PACIFIC).date().isoformat()}")
    lines.append('')
    lines.append(f"Generated: {datetime.now(PACIFIC).strftime('%Y-%m-%d %H:%M %Z')}")
    lines.append('')
    lines.append('## What this watches')
    for f in config['feeds']:
        lines.append(f"- {f['name']}")
    lines.append('')
    lines.append('## Recent episodes / links')
    if not recent:
        lines.append('- No new episodes found in the configured window.')
    for e in recent[:30]:
        ep_date = e.get('published_date') or 'link'
        tags = ', '.join(e.get('tags') or []) or 'general'
        lines.append(f"### {e['short_name']} — {e['title']}")
        lines.append(f"- Date: {ep_date}")
        if e.get('duration'):
            lines.append(f"- Duration: {e['duration']}")
        lines.append(f"- Tags: {tags}")
        if e.get('url'):
            lines.append(f"- Episode: {e.get('url')}")
        if e.get('audio_url'):
            lines.append(f"- Audio: {e.get('audio_url')}")
        if e.get('summary'):
            lines.append(f"- Summary from show notes: {e['summary']}")
        lines.append('')
    lines.append('## AI summary prompt')
    lines.append('For Scout: prioritize AI consulting, data readiness, finance/accounting, entrepreneurship, health/performance/parenting, LinkedIn content ideas, personal CRM, and cricket/sports-business angles. Return concise takeaways and suggested actions.')
    return '\n'.join(lines).strip() + '\n', recent


def json_for_docs(all_episodes, ytd_episodes, recent, config, podcasts, stats, since: date):
    return {
        'generated_at': datetime.now(PACIFIC).isoformat(),
        'since': since.isoformat(),
        'user_interests': config.get('user_interests', []),
        'podcasts': podcasts,
        'episodes': ytd_episodes,
        'recent': recent,
        'stats': stats,
    }


def write_html(all_episodes, ytd_episodes, recent, config, podcasts, stats, since: date):
    DOCS.mkdir(parents=True, exist_ok=True)
    data = json_for_docs(all_episodes, ytd_episodes, recent, config, podcasts, stats, since)
    (DOCS / 'episodes.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    html_doc = r'''<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>David Podcast OS</title>
<meta name="description" content="David's interactive podcast digest and 2026 history dashboard">
<style>
:root{--bg:#f7f8fc;--panel:#fff;--ink:#0f172a;--muted:#64748b;--line:#e5e7eb;--brand:#6d5dfc;--brand2:#06b6d4;--good:#10b981;--warn:#f59e0b;--bad:#ef4444;--shadow:0 24px 80px rgba(15,23,42,.10);--radius:24px}
*{box-sizing:border-box} html{scroll-behavior:smooth} body{margin:0;background:radial-gradient(circle at 5% -10%,#dbeafe 0,#f8fafc 26%,#f7f8fc 100%);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}.app{display:grid;grid-template-columns:280px minmax(0,1fr);min-height:100vh}.side{position:sticky;top:0;height:100vh;padding:24px 18px;border-right:1px solid var(--line);background:rgba(255,255,255,.76);backdrop-filter:blur(20px)}.brand{display:flex;gap:12px;align-items:center;font-weight:900;letter-spacing:-.04em;font-size:20px}.mark{width:42px;height:42px;border-radius:16px;background:linear-gradient(135deg,var(--brand),var(--brand2));display:grid;place-items:center;color:white;box-shadow:0 18px 40px #6d5dfc42}.nav{display:grid;gap:8px;margin:26px 0}.nav a{padding:11px 12px;border-radius:14px;text-decoration:none;color:var(--muted);font-weight:760;font-size:14px}.nav a:hover,.nav a.active{background:#eef2ff;color:var(--brand)}.sideStats{border-radius:20px;background:#f8fafc;border:1px solid var(--line);padding:14px;color:var(--muted);font-size:12px;line-height:1.45}.main{max-width:1440px;margin:0 auto;padding:28px;width:100%;min-width:0}.hero{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(340px,.75fr);gap:18px;margin-bottom:18px}.heroCard{position:relative;overflow:hidden;border-radius:32px;padding:32px;background:linear-gradient(135deg,#0f172a,#312e81 58%,#0e7490);color:white;box-shadow:var(--shadow)}.heroCard:before{content:"";position:absolute;right:-90px;top:-90px;width:280px;height:280px;background:rgba(255,255,255,.12);border-radius:999px}.heroCard h1{position:relative;font-size:48px;line-height:.98;margin:12px 0;letter-spacing:-.06em;max-width:850px}.eyebrow{position:relative;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:900;color:#bfdbfe}.heroCard p{position:relative;color:#dbeafe;line-height:1.6;font-size:16px;max-width:760px}.heroActions{position:relative;display:flex;flex-wrap:wrap;gap:10px;margin-top:20px}.btn{display:inline-flex;align-items:center;gap:8px;border:0;border-radius:999px;background:#fff;color:#1e1b4b;padding:11px 15px;font-weight:850;text-decoration:none;cursor:pointer}.btn.secondary{background:rgba(255,255,255,.14);color:white;border:1px solid rgba(255,255,255,.2)}.panel{background:rgba(255,255,255,.93);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px;min-width:0}.panel h2{margin:0 0 12px;font-size:20px;letter-spacing:-.03em}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:18px}.metric{background:rgba(255,255,255,.92);border:1px solid var(--line);box-shadow:var(--shadow);border-radius:22px;padding:18px;min-width:0}.metric .label{font-size:12px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.06em}.metric .value{font-size:32px;font-weight:920;letter-spacing:-.05em;margin-top:6px}.metric .sub{font-size:12px;color:var(--muted);margin-top:5px}.controls{display:grid;grid-template-columns:minmax(260px,1fr) 180px 180px 180px;gap:10px;margin:16px 0}.input,.select{width:100%;border:1px solid var(--line);border-radius:15px;background:#fff;padding:12px 13px;color:var(--ink);font-weight:700}.section{margin-top:18px}.podGrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.pod{position:relative;overflow:hidden;border-radius:24px;color:white;min-height:180px;padding:18px;display:flex;flex-direction:column;justify-content:space-between;background:linear-gradient(135deg,#111827,#4f46e5);box-shadow:var(--shadow)}.pod:after{content:"";position:absolute;right:-42px;top:-42px;width:150px;height:150px;background:rgba(255,255,255,.15);border-radius:999px}.pod .emoji{font-size:32px}.pod h3{margin:10px 0 4px;font-size:19px;letter-spacing:-.03em}.pod p{margin:0;color:rgba(255,255,255,.78);font-size:13px;line-height:1.4}.pill{display:inline-flex;align-items:center;border-radius:999px;background:#f1f5f9;color:#475569;padding:5px 9px;font-size:12px;font-weight:850;margin:3px}.heroCard .pill,.pod .pill{background:rgba(255,255,255,.16);color:white}.grid{display:grid;grid-template-columns:1.2fr .8fr;gap:16px}.episodeGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.episode{display:flex;flex-direction:column;gap:12px;background:white;border:1px solid var(--line);border-radius:22px;padding:15px;box-shadow:0 18px 52px rgba(15,23,42,.08);min-width:0}.episodeTop{display:flex;gap:12px}.art{width:62px;height:62px;border-radius:16px;background:linear-gradient(135deg,var(--brand),var(--brand2));object-fit:cover;flex:0 0 auto;display:grid;place-items:center;color:white;font-size:24px}.episode h3{font-size:16px;line-height:1.15;margin:0;letter-spacing:-.025em}.meta{color:var(--muted);font-size:12px;font-weight:760}.summary{color:#334155;font-size:13px;line-height:1.48;margin:0}.links{display:flex;flex-wrap:wrap;gap:8px;margin-top:auto}.miniBtn{border:1px solid var(--line);border-radius:999px;padding:7px 10px;text-decoration:none;color:var(--brand);font-size:12px;font-weight:850;background:#fff}.table{width:100%;border-collapse:collapse;font-size:13px}.table th,.table td{padding:10px;border-bottom:1px solid var(--line);text-align:left}.table th{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.barRow{display:grid;grid-template-columns:105px 1fr 44px;gap:10px;align-items:center;margin:10px 0}.bar{height:12px;background:#eef2f7;border-radius:999px;overflow:hidden}.fill{height:100%;background:linear-gradient(90deg,var(--brand),var(--brand2));border-radius:999px}.tagCloud{display:flex;flex-wrap:wrap;gap:7px}.tag{border:1px solid var(--line);border-radius:999px;padding:7px 10px;background:#fff;font-size:12px;font-weight:850;color:#475569;cursor:pointer}.tag:hover{border-color:#c7d2fe;color:var(--brand);background:#eef2ff}.timeline{display:grid;gap:10px}.month{display:grid;grid-template-columns:80px 1fr 46px;gap:10px;align-items:center}.footer{margin-top:26px;color:var(--muted);font-size:12px;line-height:1.45}.empty{padding:30px;text-align:center;color:var(--muted)}@media(max-width:1120px){.app{grid-template-columns:1fr}.side{position:relative;height:auto;border-right:0;border-bottom:1px solid var(--line)}.nav{display:flex;overflow:auto;margin:16px 0}.hero,.grid{grid-template-columns:1fr}.metrics,.podGrid{grid-template-columns:repeat(2,minmax(0,1fr))}.episodeGrid{grid-template-columns:repeat(2,minmax(0,1fr))}.controls{grid-template-columns:1fr 1fr}}@media(max-width:680px){.main{padding:14px}.heroCard{padding:22px;border-radius:24px}.heroCard h1{font-size:34px}.metrics,.podGrid,.episodeGrid,.controls{grid-template-columns:1fr}.panel,.metric,.episode{border-radius:18px}.side{padding:15px}.episodeTop{align-items:flex-start}.art{width:54px;height:54px}}
</style></head><body>
<div class="app"><aside class="side"><div class="brand"><div class="mark">🎧</div><span>Podcast OS</span></div><nav class="nav"><a class="active" href="#overview">Overview</a><a href="#podcasts">Podcasts</a><a href="#history">2026 History</a><a href="#episodes">Episodes</a><a href="#signals">Signals</a></nav><div class="sideStats" id="sideStats">Loading podcast data…</div></aside><main class="main">
<section class="hero" id="overview"><div class="heroCard"><div class="eyebrow">David's audio intelligence dashboard</div><h1>Follow the shows that feed your ideas.</h1><p id="heroCopy">Loading…</p><div class="heroActions"><a class="btn" href="#episodes">Browse episodes</a><a class="btn secondary" href="./episodes.json" target="_blank">Open data</a><a class="btn secondary" href="https://github.com/dbunn117/podcast-digest" target="_blank">GitHub</a></div><div id="interestPills" style="margin-top:18px"></div></div><div class="panel"><h2>Latest from each show</h2><div id="latestShows"></div></div></section>
<section class="metrics" id="metrics"></section>
<section class="section" id="podcasts"><div class="podGrid" id="podGrid"></div></section>
<section class="section grid" id="history"><div class="panel"><h2>2026 episode history</h2><div id="monthBars"></div></div><div class="panel"><h2>Podcast mix</h2><div id="podBars"></div></div></section>
<section class="section panel" id="episodes"><h2>Episode archive</h2><div class="controls"><input class="input" id="search" placeholder="Search topics, guests, show notes…"><select class="select" id="podFilter"><option value="all">All podcasts</option></select><select class="select" id="tagFilter"><option value="all">All themes</option></select><select class="select" id="sort"><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="podcast">Podcast</option></select></div><div id="episodeCount" class="meta"></div><div class="episodeGrid" id="episodeGrid"></div></section>
<section class="section grid" id="signals"><div class="panel"><h2>Theme signals</h2><div class="tagCloud" id="tagCloud"></div></div><div class="panel"><h2>What Scout should prioritize</h2><div id="priorities"></div></div></section>
<div class="footer">Generated as a static GitHub Pages app. Summaries are currently derived from RSS show notes; transcript extraction can be added for YouTube/Spotify/Apple links where available. Full episode/audio links are preserved where feeds expose them.</div>
</main></div>
<script>
const $=s=>document.querySelector(s); const fmt=n=>Number(n||0).toLocaleString();
const esc=s=>(s||'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));
function dateLabel(d){if(!d)return 'Link'; const x=new Date(d+'T12:00:00'); return x.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});}
function barRows(obj, total=null){const entries=Object.entries(obj||{}); const max=Math.max(...entries.map(x=>x[1]),1); return entries.map(([k,v])=>`<div class="barRow"><div class="meta">${esc(k)}</div><div class="bar"><div class="fill" style="width:${Math.max(4,100*v/max)}%"></div></div><div class="meta">${v}</div></div>`).join('')}
function episodeCard(e){const img=e.image||e.podcast_image; const tags=(e.tags||[]).slice(0,4).map(t=>`<span class="pill">${esc(t)}</span>`).join(''); return `<article class="episode"><div class="episodeTop">${img?`<img class="art" src="${esc(img)}" alt="${esc(e.short_name)} artwork" loading="lazy">`:`<div class="art" style="background:${e.accent||'#4f46e5'}">${e.emoji||'🎧'}</div>`}<div><div class="meta">${esc(e.short_name)} · ${dateLabel(e.published_date)} ${e.duration?'· '+esc(e.duration):''}</div><h3>${esc(e.title)}</h3></div></div><div>${tags}</div><p class="summary">${esc(e.summary||'No summary available from feed yet.')}</p><div class="links">${e.url?`<a class="miniBtn" href="${esc(e.url)}" target="_blank" rel="noopener">Full episode</a>`:''}${e.audio_url?`<a class="miniBtn" href="${esc(e.audio_url)}" target="_blank" rel="noopener">Audio file</a>`:''}<button class="miniBtn" onclick="copyTitle('${esc((e.short_name+' — '+e.title).replace(/'/g,'’'))}')">Copy title</button></div></article>`}
function copyTitle(t){navigator.clipboard&&navigator.clipboard.writeText(t)}
let DATA, EPISODES=[];
function renderEpisodes(){const q=$('#search').value.toLowerCase().trim(), pf=$('#podFilter').value, tf=$('#tagFilter').value, sort=$('#sort').value; let list=[...EPISODES]; if(pf!=='all')list=list.filter(e=>e.short_name===pf); if(tf!=='all')list=list.filter(e=>(e.tags||[]).includes(tf)); if(q)list=list.filter(e=>[e.title,e.summary,e.show_notes,e.short_name,(e.tags||[]).join(' ')].join(' ').toLowerCase().includes(q)); list.sort((a,b)=>sort==='oldest'?(a.published_at||'').localeCompare(b.published_at||''):sort==='podcast'?(a.short_name+a.title).localeCompare(b.short_name+b.title):(b.published_at||'').localeCompare(a.published_at||'')); $('#episodeCount').textContent=`Showing ${list.length} of ${EPISODES.length} 2026 episodes`; $('#episodeGrid').innerHTML=list.length?list.slice(0,120).map(episodeCard).join(''):'<div class="empty">No matching episodes. Try another filter.</div>';}
async function main(){DATA=await fetch('./episodes.json').then(r=>r.json()); EPISODES=DATA.episodes||[]; const s=DATA.stats||{}, pods=DATA.podcasts||[], latest=s.latest_by_podcast||{}; $('#sideStats').innerHTML=`Generated<br>${new Date(DATA.generated_at).toLocaleString()}<br><br><b>${fmt(s.ytd_count)}</b> YTD episodes<br><b>${fmt(s.podcast_count)}</b> favorite podcasts<br><b>${fmt(s.link_inbox_count)}</b> saved links`; $('#heroCopy').textContent=`Interactive 2026 YTD history for ${s.podcast_count} favorite shows, with searchable summaries, full episode links, audio links where available, and theme signals for AI consulting, health, business, content, and cricket.`; $('#interestPills').innerHTML=(DATA.user_interests||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join(''); $('#metrics').innerHTML=[['YTD episodes',s.ytd_count,'2026 feed history'],['Available episodes',s.total_available,'all RSS-visible history'],['Recent items',s.recent_count,'daily digest window'],['Saved links',s.link_inbox_count,'Obsidian link inbox']].map(x=>`<div class="metric"><div class="label">${x[0]}</div><div class="value">${fmt(x[1])}</div><div class="sub">${x[2]}</div></div>`).join(''); $('#podGrid').innerHTML=pods.map(p=>{const c=s.by_podcast?.[p.short_name]||0, g=p.gradient||['#111827','#4f46e5']; return `<div class="pod" style="background:linear-gradient(135deg,${g[0]},${g[1]})"><div><div class="emoji">${p.emoji||'🎧'}</div><h3>${esc(p.short_name)}</h3><p>${esc((p.themes||[]).join(' · '))}</p></div><div><span class="pill">${c} YTD episodes</span></div></div>`}).join(''); $('#latestShows').innerHTML=Object.values(latest).map(e=>`<div class="episode" style="box-shadow:none;margin-bottom:10px"><div class="meta">${esc(e.short_name)} · ${dateLabel(e.published_date)}</div><b>${esc(e.title)}</b>${e.url?`<div><a class="miniBtn" href="${esc(e.url)}" target="_blank">Open</a></div>`:''}</div>`).join(''); $('#monthBars').innerHTML=barRows(s.by_month||{}); $('#podBars').innerHTML=barRows(s.by_podcast||{}); const podcasts=[...new Set(EPISODES.map(e=>e.short_name))].sort(); $('#podFilter').innerHTML='<option value="all">All podcasts</option>'+podcasts.map(p=>`<option>${esc(p)}</option>`).join(''); const tags=[...new Set(EPISODES.flatMap(e=>e.tags||[]))].sort(); $('#tagFilter').innerHTML='<option value="all">All themes</option>'+tags.map(t=>`<option>${esc(t)}</option>`).join(''); $('#tagCloud').innerHTML=Object.entries(s.by_tag||{}).map(([t,c])=>`<button class="tag" onclick="document.querySelector('#tagFilter').value='${esc(t)}';renderEpisodes();location.hash='episodes'">${esc(t)} · ${c}</button>`).join(''); $('#priorities').innerHTML=(DATA.user_interests||[]).map(x=>`<div class="episode" style="box-shadow:none;margin-bottom:10px"><b>${esc(x)}</b><p class="summary">Use this as a lens when turning episodes into takeaways, content ideas, or follow-up actions.</p></div>`).join(''); ['search','podFilter','tagFilter','sort'].forEach(id=>$('#'+id).addEventListener('input',renderEpisodes)); renderEpisodes();}
main().catch(err=>{document.body.innerHTML='<pre style="padding:20px">Dashboard error: '+esc(String(err))+'</pre>'})
</script></body></html>'''
    (DOCS / 'index.html').write_text(html_doc, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=2, help='Recent digest window')
    ap.add_argument('--since', default='2026-01-01', help='Dashboard history start date, YYYY-MM-DD')
    ap.add_argument('--write', action='store_true', help='Write Obsidian daily digest')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    config = load_config()
    since = date.fromisoformat(args.since)
    episodes: list[dict] = []
    podcasts: list[dict] = []
    errors = []
    for feed in config['feeds']:
        try:
            eps, meta = parse_feed(feed)
            episodes.extend(eps)
            podcasts.append(meta)
        except Exception as e:
            errors.append({'feed': feed['name'], 'error': f'{type(e).__name__}: {e}'})
            podcasts.append({'name': feed['name'], 'short_name': feed.get('short_name') or feed['name'], 'feed_url': feed['feed_url'], 'themes': feed.get('themes') or [], **PODCAST_ACCENTS.get(feed.get('short_name') or feed['name'], {})})

    episodes.extend(read_link_inbox(Path(config['link_inbox'])))

    seen = set(); dedup = []
    for e in sorted(episodes, key=episode_sort_key, reverse=True):
        key = e.get('guid') or e.get('url') or e.get('title')
        if key in seen:
            continue
        seen.add(key); dedup.append(e)

    ytd = filter_since(dedup, since)
    digest, recent = build_digest(dedup, args.days, config)
    stats = build_stats(dedup, ytd, recent, podcasts)

    (DATA / 'episodes.json').write_text(json.dumps({'episodes': dedup, 'ytd': ytd, 'podcasts': podcasts, 'stats': stats, 'errors': errors}, indent=2), encoding='utf-8')
    (DATA / 'latest_digest.md').write_text(digest, encoding='utf-8')
    write_html(dedup, ytd, recent, config, podcasts, stats, since)

    if args.write:
        vault = Path(config['obsidian_vault'])
        outdir = vault / '08 Podcasts' / 'Daily Digests'
        outdir.mkdir(parents=True, exist_ok=True)
        out = outdir / (datetime.now(PACIFIC).date().isoformat() + ' Podcast Digest.md')
        out.write_text(digest, encoding='utf-8')

    result = {
        'generated_at': datetime.now(PACIFIC).isoformat(),
        'episode_count': len(dedup),
        'ytd_count': len(ytd),
        'recent_count': len(recent),
        'podcast_count': len(podcasts),
        'errors': errors,
        'digest_path': str(DATA / 'latest_digest.md'),
        'dashboard': str(DOCS / 'index.html'),
    }
    if args.json:
        compact_recent = [
            {
                'podcast': e.get('short_name'),
                'title': e.get('title'),
                'published_date': e.get('published_date'),
                'duration': e.get('duration'),
                'tags': e.get('tags', []),
                'url': e.get('url'),
                'audio_url': e.get('audio_url'),
                'summary': e.get('summary'),
            }
            for e in recent[:20]
        ]
        print(json.dumps({'result': result, 'recent': compact_recent, 'stats': stats}, indent=2))
    else:
        print(json.dumps(result, indent=2))
        print('\n' + digest[:7000])


if __name__ == '__main__':
    main()
