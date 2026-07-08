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
    'TWIST': {'accent': '#0ea5e9', 'gradient': ['#0c4a6e', '#38bdf8'], 'emoji': '⚡'},
    'Prof G Markets': {'accent': '#22c55e', 'gradient': ['#052e16', '#16a34a'], 'emoji': '📈'},
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
    html_doc = '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>David Podcast OS</title>\n<meta name="description" content="David\'s interactive podcast digest and 2026 history dashboard">\n<link rel="preconnect" href="https://fonts.googleapis.com">\n<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">\n<style>\n:root{--bg:#080b14;--bg2:#0d1324;--panel:rgba(255,255,255,.075);--panel2:rgba(255,255,255,.115);--ink:#f8fafc;--muted:#9aa7bd;--soft:#cbd5e1;--line:rgba(255,255,255,.13);--brand:#8b5cf6;--brand2:#06b6d4;--brand3:#22c55e;--warn:#f59e0b;--shadow:0 24px 100px rgba(0,0,0,.35);--radius:28px;--sidebar:292px}\n*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-height:100vh;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#080b14;background-image:radial-gradient(circle at 10% -10%,rgba(139,92,246,.45),transparent 32%),radial-gradient(circle at 80% 0,rgba(6,182,212,.34),transparent 34%),linear-gradient(135deg,#070a12 0,#0b1020 45%,#101827 100%);background-attachment:fixed;overflow-x:hidden}body:before{content:"";position:fixed;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:54px 54px;mask-image:linear-gradient(to bottom,rgba(0,0,0,.75),transparent 75%);pointer-events:none}.app{display:grid;grid-template-columns:var(--sidebar) minmax(0,1fr);min-height:100vh}.side{position:sticky;top:0;height:100vh;padding:22px;border-right:1px solid var(--line);background:linear-gradient(180deg,rgba(8,11,20,.92),rgba(8,11,20,.72));backdrop-filter:blur(24px);z-index:10}.brand{display:flex;gap:12px;align-items:center;font-weight:900;letter-spacing:-.05em;font-size:21px}.mark{width:46px;height:46px;border-radius:16px;background:conic-gradient(from 220deg,var(--brand),var(--brand2),var(--brand3),var(--brand));display:grid;place-items:center;box-shadow:0 18px 55px rgba(139,92,246,.42)}.brand small{display:block;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:800;margin-top:2px}.nav{display:grid;gap:8px;margin:28px 0}.nav a{display:flex;align-items:center;gap:10px;padding:12px 13px;border-radius:16px;text-decoration:none;color:var(--muted);font-weight:800;font-size:14px;border:1px solid transparent}.nav a:hover,.nav a.active{background:rgba(255,255,255,.09);border-color:var(--line);color:#fff}.sideStats{border-radius:22px;background:linear-gradient(180deg,rgba(255,255,255,.10),rgba(255,255,255,.05));border:1px solid var(--line);padding:16px;color:var(--muted);font-size:12px;line-height:1.55;box-shadow:var(--shadow)}.sideStats b{color:#fff;font-size:20px}.main{width:100%;min-width:0;padding:26px;position:relative}.shell{max-width:1480px;margin:0 auto}.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.status{display:flex;gap:10px;align-items:center;color:var(--muted);font-size:13px;font-weight:700}.dot{width:9px;height:9px;border-radius:50%;background:var(--brand3);box-shadow:0 0 0 7px rgba(34,197,94,.12)}.topActions{display:flex;gap:10px;flex-wrap:wrap}.hero{display:grid;grid-template-columns:minmax(0,1.16fr) minmax(350px,.84fr);gap:18px;margin-bottom:18px}.card,.panel,.metric,.episode{border:1px solid var(--line);background:linear-gradient(180deg,rgba(20,31,52,.92),rgba(15,23,42,.82));backdrop-filter:blur(20px);box-shadow:var(--shadow)}.heroCard{position:relative;overflow:hidden;border-radius:34px;padding:38px;min-height:445px;background:linear-gradient(135deg,rgba(15,23,42,.96),rgba(49,46,129,.78) 48%,rgba(8,145,178,.62));display:flex;flex-direction:column;justify-content:space-between}.heroCard:before{content:"";position:absolute;right:-120px;top:-120px;width:360px;height:360px;border-radius:999px;background:radial-gradient(circle,rgba(255,255,255,.28),rgba(255,255,255,.04) 58%,transparent 70%)}.heroCard:after{content:"";position:absolute;inset:auto -10% -35% 8%;height:260px;background:linear-gradient(90deg,rgba(139,92,246,.26),rgba(6,182,212,.22));filter:blur(50px);transform:rotate(-7deg)}.heroContent{position:relative;z-index:1}.eyebrow{display:inline-flex;gap:9px;align-items:center;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:900;color:#dbeafe;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.16);border-radius:999px;padding:8px 11px}.heroCard h1{font-size:clamp(44px,5vw,82px);line-height:.88;margin:18px 0;letter-spacing:-.08em;max-width:920px}.heroCard p{color:#dbeafe;line-height:1.65;font-size:17px;max-width:800px}.heroActions,.links{display:flex;gap:10px;flex-wrap:wrap}.btn,.miniBtn{appearance:none;border:0;text-decoration:none;cursor:pointer;font:inherit;font-weight:900}.btn{display:inline-flex;align-items:center;gap:9px;padding:13px 16px;border-radius:16px;background:#fff;color:#0f172a;box-shadow:0 16px 40px rgba(0,0,0,.18)}.btn.secondary{background:rgba(255,255,255,.12);color:#fff;border:1px solid rgba(255,255,255,.18);box-shadow:none}.btn:hover,.miniBtn:hover{transform:translateY(-1px)}.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:7px 10px;font-size:12px;font-weight:800;color:#dbeafe;background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.14);margin:4px}.panel{border-radius:var(--radius);padding:22px}.panel h2,.section h2{margin:0 0 16px;font-size:20px;letter-spacing:-.03em}.latestStack{display:grid;gap:10px}.latestItem{display:grid;grid-template-columns:42px minmax(0,1fr);gap:12px;align-items:start;padding:12px;border-radius:18px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1)}.latestIcon{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;background:rgba(255,255,255,.1);overflow:hidden}.latestIcon img{width:100%;height:100%;object-fit:cover}.latestItem b{display:block;line-height:1.25}.meta{color:var(--muted);font-size:12px;font-weight:800;letter-spacing:.01em}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}.metric{border-radius:24px;padding:20px;position:relative;overflow:hidden}.metric:after{content:"";position:absolute;right:-30px;top:-30px;width:90px;height:90px;border-radius:999px;background:rgba(255,255,255,.08)}.metric .label{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.12em;font-weight:900}.metric .value{font-size:40px;font-weight:950;letter-spacing:-.06em;margin:8px 0}.metric .sub{color:var(--soft);font-size:13px}.section{margin:18px 0}.sectionHead{display:flex;justify-content:space-between;align-items:end;gap:18px;margin:0 0 14px}.sectionHead p{margin:4px 0 0;color:var(--muted)}.podGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.pod{position:relative;min-height:205px;border-radius:28px;padding:22px;color:white;overflow:hidden;border:1px solid rgba(255,255,255,.16);box-shadow:var(--shadow);display:flex;flex-direction:column;justify-content:space-between}.pod:before{content:"";position:absolute;right:-55px;top:-55px;width:160px;height:160px;border-radius:50%;background:rgba(255,255,255,.16)}.pod>*{position:relative}.emoji{font-size:34px;margin-bottom:12px}.pod h3{font-size:26px;letter-spacing:-.05em;margin:0}.pod p{color:rgba(255,255,255,.78);line-height:1.5}.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.barRow{display:grid;grid-template-columns:92px minmax(0,1fr) 44px;gap:10px;align-items:center;margin:11px 0}.bar{height:12px;border-radius:999px;background:rgba(255,255,255,.09);overflow:hidden}.fill{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--brand),var(--brand2));box-shadow:0 0 24px rgba(6,182,212,.35)}.controls{position:sticky;top:14px;z-index:5;display:grid;grid-template-columns:minmax(260px,1.25fr) repeat(3,minmax(150px,.55fr));gap:10px;margin:8px 0 14px;padding:10px;border-radius:22px;background:rgba(8,11,20,.78);border:1px solid var(--line);backdrop-filter:blur(18px)}.input,.select{width:100%;border:1px solid var(--line);background:rgba(255,255,255,.08);color:#fff;border-radius:15px;padding:13px 14px;font:inherit;font-weight:700;outline:none}.input::placeholder{color:#718096}.select option{background:#0f172a;color:#fff}.episodeGrid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.episode{border-radius:24px;padding:16px;transition:.18s ease transform,.18s ease border-color;overflow:hidden}.episode:hover{transform:translateY(-3px);border-color:rgba(255,255,255,.24)}.episodeTop{display:grid;grid-template-columns:70px minmax(0,1fr);gap:13px;align-items:start}.art{width:70px;height:70px;border-radius:18px;object-fit:cover;display:grid;place-items:center;color:#fff;font-size:26px;box-shadow:0 18px 35px rgba(0,0,0,.22)}.episode h3{font-size:18px;line-height:1.18;letter-spacing:-.035em;margin:4px 0 0}.summary{color:var(--soft);font-size:13.5px;line-height:1.56;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}.miniBtn{display:inline-flex;align-items:center;gap:6px;padding:9px 11px;border-radius:12px;background:rgba(255,255,255,.09);color:#eef2ff;border:1px solid rgba(255,255,255,.12);font-size:12px}.tagCloud{display:flex;gap:9px;flex-wrap:wrap}.tag{border:1px solid var(--line);background:rgba(255,255,255,.08);color:#e2e8f0;border-radius:999px;padding:10px 12px;font-weight:900;cursor:pointer}.tag:hover{background:rgba(255,255,255,.14)}.empty,.footer{color:var(--muted);text-align:center;padding:22px}.footer{font-size:12px}\n@media(max-width:1150px){.app{grid-template-columns:1fr}.side{position:relative;height:auto}.nav{display:flex;overflow:auto}.main{padding:18px}.hero,.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.podGrid,.episodeGrid{grid-template-columns:repeat(2,1fr)}.controls{position:relative;top:0;grid-template-columns:1fr 1fr}.topbar{display:none}}\n@media(max-width:720px){.side{padding:16px}.heroCard{padding:24px;min-height:380px}.metrics,.podGrid,.episodeGrid,.controls{grid-template-columns:1fr}.heroCard h1{font-size:44px}.main{padding:12px}.sectionHead{display:block}}\n</style>\n</head>\n<body>\n<div class="app">\n  <aside class="side">\n    <div class="brand"><div class="mark">🎧</div><div>Podcast OS<small>Audio intelligence</small></div></div>\n    <nav class="nav"><a class="active" href="#overview">◆ Overview</a><a href="#podcasts">◐ Shows</a><a href="#history">▦ History</a><a href="#episodes">⌕ Episodes</a><a href="#signals">✦ Signals</a></nav>\n    <div class="sideStats" id="sideStats">Loading podcast data…</div>\n  </aside>\n  <main class="main"><div class="shell">\n    <div class="topbar"><div class="status"><span class="dot"></span><span>Live from RSS feeds · GitHub Pages static app</span></div><div class="topActions"><a class="btn secondary" href="./episodes.json" target="_blank">Open data</a><a class="btn secondary" href="https://github.com/dbunn117/podcast-digest" target="_blank">GitHub</a></div></div>\n    <section class="hero" id="overview">\n      <div class="heroCard card"><div class="heroContent"><div class="eyebrow">✦ David\'s audio intelligence dashboard</div><h1>Turn favorite podcasts into searchable signal.</h1><p id="heroCopy">Loading…</p><div class="heroActions"><a class="btn" href="#episodes">Browse archive →</a><a class="btn secondary" href="#signals">View signals</a></div></div><div id="interestPills"></div></div>\n      <div class="panel"><div class="sectionHead"><div><h2>Latest from each show</h2><p>Freshest episode per feed</p></div></div><div id="latestShows" class="latestStack"></div></div>\n    </section>\n    <section class="metrics" id="metrics"></section>\n    <section class="section" id="podcasts"><div class="sectionHead"><div><h2>Favorite shows</h2><p>Curated feeds feeding David\'s AI, finance, health, and sports-business idea flow.</p></div></div><div class="podGrid" id="podGrid"></div></section>\n    <section class="section grid" id="history"><div class="panel"><h2>2026 episode history</h2><div id="monthBars"></div></div><div class="panel"><h2>Podcast mix</h2><div id="podBars"></div></div></section>\n    <section class="section panel" id="episodes"><div class="sectionHead"><div><h2>Episode archive</h2><p>Search titles, show notes, guests, podcasts, and themes.</p></div><div id="episodeCount" class="meta"></div></div><div class="controls"><input class="input" id="search" placeholder="Search: AI agents, markets, sleep, cricket…"><select class="select" id="podFilter"><option value="all">All podcasts</option></select><select class="select" id="tagFilter"><option value="all">All themes</option></select><select class="select" id="sort"><option value="newest">Newest first</option><option value="oldest">Oldest first</option><option value="podcast">Podcast</option></select></div><div class="episodeGrid" id="episodeGrid"></div></section>\n    <section class="section grid" id="signals"><div class="panel"><h2>Theme signals</h2><div class="tagCloud" id="tagCloud"></div></div><div class="panel"><h2>Scout priority lenses</h2><div id="priorities"></div></div></section>\n    <div class="footer">Static SaaS-style dashboard generated from RSS show notes. Full episode/audio links are preserved where feeds expose them.</div>\n  </div></main>\n</div>\n<script>\nconst $=s=>document.querySelector(s); const fmt=n=>Number(n||0).toLocaleString();\nconst esc=s=>(s||\'\').replace(/[&<>"\']/g,m=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\',"\'":\'&#039;\'}[m]));\nfunction dateLabel(d){if(!d)return \'Link\'; const x=new Date(d+\'T12:00:00\'); return x.toLocaleDateString(undefined,{month:\'short\',day:\'numeric\',year:\'numeric\'});}\nfunction barRows(obj){const entries=Object.entries(obj||{}); const max=Math.max(...entries.map(x=>x[1]),1); return entries.map(([k,v])=>`<div class="barRow"><div class="meta">${esc(k)}</div><div class="bar"><div class="fill" style="width:${Math.max(5,100*v/max)}%"></div></div><div class="meta">${fmt(v)}</div></div>`).join(\'\')}\nfunction episodeCard(e){const img=e.image||e.podcast_image; const tags=(e.tags||[]).slice(0,5).map(t=>`<span class="pill">${esc(t)}</span>`).join(\'\'); return `<article class="episode"><div class="episodeTop">${img?`<img class="art" src="${esc(img)}" alt="${esc(e.short_name)} artwork" loading="lazy">`:`<div class="art" style="background:${e.accent||\'#4f46e5\'}">${e.emoji||\'🎧\'}</div>`}<div><div class="meta">${esc(e.short_name)} · ${dateLabel(e.published_date)} ${e.duration?\'· \'+esc(e.duration):\'\'}</div><h3>${esc(e.title)}</h3></div></div><div style="margin-top:12px">${tags}</div><p class="summary">${esc(e.summary||\'No summary available from feed yet.\')}</p><div class="links">${e.url?`<a class="miniBtn" href="${esc(e.url)}" target="_blank" rel="noopener">↗ Full episode</a>`:\'\'}${e.audio_url?`<a class="miniBtn" href="${esc(e.audio_url)}" target="_blank" rel="noopener">▶ Audio</a>`:\'\'}<button class="miniBtn" onclick="copyTitle(\'${esc((e.short_name+\' — \'+e.title).replace(/\'/g,\'’\'))}\')">Copy</button></div></article>`}\nfunction copyTitle(t){navigator.clipboard&&navigator.clipboard.writeText(t)}\nlet DATA, EPISODES=[];\nfunction renderEpisodes(){const q=$(\'#search\').value.toLowerCase().trim(), pf=$(\'#podFilter\').value, tf=$(\'#tagFilter\').value, sort=$(\'#sort\').value; let list=[...EPISODES]; if(pf!==\'all\')list=list.filter(e=>e.short_name===pf); if(tf!==\'all\')list=list.filter(e=>(e.tags||[]).includes(tf)); if(q)list=list.filter(e=>[e.title,e.summary,e.show_notes,e.short_name,(e.tags||[]).join(\' \')].join(\' \').toLowerCase().includes(q)); list.sort((a,b)=>sort===\'oldest\'?(a.published_at||\'\').localeCompare(b.published_at||\'\'):sort===\'podcast\'?(a.short_name+a.title).localeCompare(b.short_name+b.title):(b.published_at||\'\').localeCompare(a.published_at||\'\')); $(\'#episodeCount\').textContent=`Showing ${fmt(list.length)} of ${fmt(EPISODES.length)} episodes`; $(\'#episodeGrid\').innerHTML=list.length?list.slice(0,150).map(episodeCard).join(\'\'):\'<div class="empty">No matching episodes. Try another filter.</div>\';}\nasync function main(){DATA=await fetch(\'./episodes.json?v=\'+Date.now(),{cache:\'no-store\'}).then(r=>r.json()); EPISODES=DATA.episodes||[]; const s=DATA.stats||{}, pods=DATA.podcasts||[], latest=s.latest_by_podcast||{}; $(\'#sideStats\').innerHTML=`Generated<br>${new Date(DATA.generated_at).toLocaleString()}<br><br><b>${fmt(s.ytd_count)}</b> YTD episodes<br><b>${fmt(s.podcast_count)}</b> favorite podcasts<br><b>${fmt(s.link_inbox_count)}</b> saved links`; $(\'#heroCopy\').textContent=`A modern, searchable 2026 YTD command center for ${s.podcast_count} favorite shows — ${fmt(s.ytd_count)} episodes with summaries, artwork, episode links, audio links, and theme signals for AI consulting, markets, health, content, and sports.`; $(\'#interestPills\').innerHTML=(DATA.user_interests||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join(\'\'); $(\'#metrics\').innerHTML=[[\'YTD episodes\',s.ytd_count,\'2026 feed history\'],[\'Available episodes\',s.total_available,\'RSS-visible archive\'],[\'Recent items\',s.recent_count,\'daily digest window\'],[\'Saved links\',s.link_inbox_count,\'Obsidian inbox\']].map(x=>`<div class="metric"><div class="label">${x[0]}</div><div class="value">${fmt(x[1])}</div><div class="sub">${x[2]}</div></div>`).join(\'\'); $(\'#podGrid\').innerHTML=pods.map(p=>{const c=s.by_podcast?.[p.short_name]||0, g=p.gradient||[\'#111827\',\'#4f46e5\']; return `<div class="pod" style="background:linear-gradient(135deg,${g[0]},${g[1]})"><div><div class="emoji">${p.emoji||\'🎧\'}</div><h3>${esc(p.short_name)}</h3><p>${esc((p.themes||[]).join(\' · \'))}</p></div><div><span class="pill">${fmt(c)} YTD episodes</span></div></div>`}).join(\'\'); $(\'#latestShows\').innerHTML=Object.values(latest).map(e=>{const img=e.image||e.podcast_image; return `<div class="latestItem"><div class="latestIcon">${img?`<img src="${esc(img)}" alt="">`:e.emoji||\'🎧\'}</div><div><div class="meta">${esc(e.short_name)} · ${dateLabel(e.published_date)}</div><b>${esc(e.title)}</b><div class="links" style="margin-top:8px">${e.url?`<a class="miniBtn" href="${esc(e.url)}" target="_blank">Open</a>`:\'\'}${e.audio_url?`<a class="miniBtn" href="${esc(e.audio_url)}" target="_blank">Audio</a>`:\'\'}</div></div></div>`}).join(\'\'); $(\'#monthBars\').innerHTML=barRows(s.by_month||{}); $(\'#podBars\').innerHTML=barRows(s.by_podcast||{}); const podcasts=[...new Set(EPISODES.map(e=>e.short_name))].sort(); $(\'#podFilter\').innerHTML=\'<option value="all">All podcasts</option>\'+podcasts.map(p=>`<option>${esc(p)}</option>`).join(\'\'); const tags=[...new Set(EPISODES.flatMap(e=>e.tags||[]))].sort(); $(\'#tagFilter\').innerHTML=\'<option value="all">All themes</option>\'+tags.map(t=>`<option>${esc(t)}</option>`).join(\'\'); $(\'#tagCloud\').innerHTML=Object.entries(s.by_tag||{}).map(([t,c])=>`<button class="tag" onclick="document.querySelector(\'#tagFilter\').value=\'${esc(t)}\';renderEpisodes();location.hash=\'episodes\'">${esc(t)} · ${fmt(c)}</button>`).join(\'\'); $(\'#priorities\').innerHTML=(DATA.user_interests||[]).map(x=>`<div class="latestItem"><div class="latestIcon">✦</div><div><b>${esc(x)}</b><p class="summary" style="margin:6px 0 0">Use this lens when turning episodes into takeaways, content ideas, follow-ups, or experiments.</p></div></div>`).join(\'\'); [\'search\',\'podFilter\',\'tagFilter\',\'sort\'].forEach(id=>$(\'#\'+id).addEventListener(\'input\',renderEpisodes)); renderEpisodes();}\nmain().catch(err=>{document.body.innerHTML=\'<pre style="padding:20px;color:white;background:#0f172a;min-height:100vh">Dashboard error: \'+esc(String(err))+\'</pre>\'})\n</script>\n</body>\n</html>'
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
