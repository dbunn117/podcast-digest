#!/usr/bin/env python3
"""Podcast Digest MVP for David.

Fetches favorite podcast RSS feeds, tracks recent episodes, reads a link inbox,
and writes:
- /root/podcast-digest/data/episodes.json
- /root/podcast-digest/data/latest_digest.md
- /root/podcast-digest/docs/index.html
- Obsidian daily digest note under 08 Podcasts/Daily Digests/

This script deliberately produces extractive summaries from RSS show notes.
The Hermes daily cron can then turn this data into a higher-quality AI summary.
"""
from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path('/root/podcast-digest')
CONFIG = ROOT / 'config.json'
DATA = ROOT / 'data'
DOCS = ROOT / 'docs'
TZ = timezone(timedelta(hours=-7), 'PDT')  # current deployment is Pacific-focused

KEYWORDS = {
    'ai': ['ai', 'artificial intelligence', 'llm', 'openai', 'anthropic', 'agent', 'automation'],
    'business': ['business', 'startup', 'founder', 'revenue', 'sales', 'market', 'saas', 'consulting'],
    'career': ['career', 'job', 'work', 'operator', 'leadership'],
    'health': ['health', 'sleep', 'fitness', 'diet', 'metabolism', 'parenting', 'stress', 'exercise'],
    'content': ['content', 'creator', 'linkedin', 'storytelling', 'audience'],
    'cricket': ['cricket', 'test', 'odi', 't20', 'world cup', 'south africa', 'proteas', 'mlc']
}


def strip_html(s: str) -> str:
    s = re.sub(r'<(script|style).*?</\1>', ' ', s or '', flags=re.S|re.I)
    s = re.sub(r'<br\s*/?>', '\n', s, flags=re.I)
    s = re.sub(r'</p\s*>', '\n', s, flags=re.I)
    s = re.sub(r'<.*?>', ' ', s)
    s = html.unescape(s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def fetch_url(url: str, timeout=25) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': 'ScoutPodcastDigest/1.0 (+https://github.com/dbunn117)'})
    return urllib.request.urlopen(req, timeout=timeout).read()


def parse_date(s: str | None):
    if not s:
        return None
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(timezone.utc)
    except Exception:
        return None


def tag_text(title: str, notes: str) -> list[str]:
    text = (title + ' ' + notes).lower()
    tags=[]
    for tag, words in KEYWORDS.items():
        for w in words:
            if re.search(r'(?<![A-Za-z0-9])' + re.escape(w) + r'(?![A-Za-z0-9])', text):
                tags.append(tag)
                break
    return tags[:6]


def short_summary(text: str, max_chars=520) -> str:
    text = strip_html(text)
    if not text:
        return ''
    sentences = re.split(r'(?<=[.!?])\s+', text)
    picked=[]
    for s in sentences:
        if len(' '.join(picked)) > max_chars: break
        if 30 <= len(s) <= 260:
            picked.append(s)
        if len(picked) >= 3: break
    out=' '.join(picked) or text[:max_chars]
    return out[:max_chars].rstrip()


def parse_feed(feed):
    raw = fetch_url(feed['feed_url'])
    root = ET.fromstring(raw)
    channel = root.find('channel')
    items = channel.findall('item') if channel is not None else root.findall('.//item')
    episodes=[]
    ns = {'itunes':'http://www.itunes.com/dtds/podcast-1.0.dtd', 'content':'http://purl.org/rss/1.0/modules/content/'}
    for item in items[:30]:
        title = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        guid = (item.findtext('guid') or link or title).strip()
        pub = parse_date(item.findtext('pubDate') or item.findtext('published') or item.findtext('updated'))
        desc = item.findtext('description') or item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or ''
        duration = item.findtext('{http://www.itunes.com/dtds/podcast-1.0.dtd}duration') or ''
        clean = strip_html(desc)
        episodes.append({
            'podcast': feed['name'],
            'short_name': feed.get('short_name') or feed['name'],
            'title': title,
            'url': link,
            'guid': guid,
            'published_at': pub.isoformat() if pub else None,
            'published_date': pub.date().isoformat() if pub else None,
            'duration': duration,
            'summary': short_summary(clean),
            'show_notes': clean[:4000],
            'tags': tag_text(title, clean),
            'source': 'favorite_feed'
        })
    return episodes


def read_link_inbox(path: Path) -> list[dict]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Podcast Link Inbox\n\nSend podcast links to Scout and they can be added here for summarization.\n\n## Links\n\n', encoding='utf-8')
    txt = path.read_text(encoding='utf-8')
    urls = re.findall(r'https?://[^\s)\]>]+', txt)
    out=[]
    for url in urls:
        out.append({
            'podcast': 'User-sent link',
            'short_name': 'Link inbox',
            'title': url,
            'url': url,
            'guid': url,
            'published_at': None,
            'published_date': None,
            'duration': '',
            'summary': 'Podcast/audio link sent by David. Needs source-specific transcript or show-note extraction.',
            'show_notes': '',
            'tags': tag_text(url, ''),
            'source': 'link_inbox'
        })
    return out


def episode_sort_key(e):
    return e.get('published_at') or ''


def load_config():
    return json.loads(CONFIG.read_text())


def build_digest(episodes, days: int, config):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    recent=[]
    for e in episodes:
        pub = parse_date(e.get('published_at')) if e.get('published_at') else None
        if e.get('source') == 'link_inbox' or (pub and pub >= cutoff):
            recent.append(e)
    recent.sort(key=episode_sort_key, reverse=True)
    lines=[]
    lines.append(f"# Podcast Digest - {datetime.now(TZ).date().isoformat()}")
    lines.append('')
    lines.append(f"Generated: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M %Z')}")
    lines.append('')
    lines.append('## What this watches')
    for f in config['feeds']:
        lines.append(f"- {f['name']}")
    lines.append('')
    lines.append('## Recent episodes / links')
    if not recent:
        lines.append('- No new episodes found in the configured window.')
    for e in recent:
        date = e.get('published_date') or 'link'
        tags = ', '.join(e.get('tags') or []) or 'general'
        lines.append(f"### {e['short_name']} — {e['title']}")
        lines.append(f"- Date: {date}")
        if e.get('duration'): lines.append(f"- Duration: {e['duration']}")
        lines.append(f"- Tags: {tags}")
        lines.append(f"- Link: {e.get('url') or ''}")
        if e.get('summary'):
            lines.append(f"- Summary from show notes: {e['summary']}")
        lines.append('')
    lines.append('## AI summary prompt')
    lines.append('For Scout: prioritize AI consulting, data readiness, finance/accounting, entrepreneurship, health/performance/parenting, LinkedIn content ideas, personal CRM, and cricket/sports-business angles. Return concise takeaways and suggested actions.')
    return '\n'.join(lines).strip() + '\n', recent


def write_html(all_episodes, recent):
    DOCS.mkdir(parents=True, exist_ok=True)
    data = {'generated_at': datetime.now(TZ).isoformat(), 'episodes': all_episodes, 'recent': recent}
    (DOCS/'episodes.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    html_doc = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Podcast Digest</title><style>
body{margin:0;background:#f6f8fb;color:#0f172a;font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif}.wrap{max-width:1050px;margin:auto;padding:24px}.hero{background:linear-gradient(135deg,#111827,#4f46e5);color:white;border-radius:24px;padding:24px;margin-bottom:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}.card{background:white;border:1px solid #e2e8f0;border-radius:18px;padding:16px;box-shadow:0 14px 40px rgba(15,23,42,.08)}.muted{color:#64748b;font-size:13px}.pill{display:inline-block;background:#eef2ff;color:#4f46e5;padding:4px 8px;border-radius:999px;font-size:12px;margin:3px 4px 3px 0}a{color:#4f46e5}.summary{font-size:14px;line-height:1.45}</style></head><body><div class="wrap"><div class="hero"><h1>Podcast Digest</h1><p>Favorite podcast tracker + link inbox for David.</p></div><div id="app" class="grid"></div><script>
fetch('./episodes.json').then(r=>r.json()).then(data=>{const app=document.getElementById('app'); const eps=data.recent.length?data.recent:data.episodes.slice(0,20); app.innerHTML=eps.map(e=>`<div class="card"><div class="muted">${e.short_name||e.podcast} · ${e.published_date||'link'}</div><h3>${e.title}</h3><div>${(e.tags||[]).map(t=>`<span class="pill">${t}</span>`).join('')}</div><p class="summary">${e.summary||''}</p><p><a href="${e.url||'#'}" target="_blank">Open</a></p></div>`).join('')})
</script></div></body></html>'''
    (DOCS/'index.html').write_text(html_doc, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=2)
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    config=load_config()
    episodes=[]
    errors=[]
    for feed in config['feeds']:
        try:
            episodes.extend(parse_feed(feed))
        except Exception as e:
            errors.append({'feed': feed['name'], 'error': f'{type(e).__name__}: {e}'})
    episodes.extend(read_link_inbox(Path(config['link_inbox'])))
    # dedupe by guid/url/title
    seen=set(); dedup=[]
    for e in sorted(episodes, key=episode_sort_key, reverse=True):
        key=e.get('guid') or e.get('url') or e.get('title')
        if key in seen: continue
        seen.add(key); dedup.append(e)
    digest, recent = build_digest(dedup, args.days, config)
    (DATA/'episodes.json').write_text(json.dumps({'episodes': dedup, 'errors': errors}, indent=2), encoding='utf-8')
    (DATA/'latest_digest.md').write_text(digest, encoding='utf-8')
    write_html(dedup, recent)
    if args.write:
        vault=Path(config['obsidian_vault'])
        outdir=vault/'08 Podcasts'/'Daily Digests'
        outdir.mkdir(parents=True, exist_ok=True)
        out=(outdir/(datetime.now(TZ).date().isoformat() + ' Podcast Digest.md'))
        out.write_text(digest, encoding='utf-8')
    result={'generated_at': datetime.now(TZ).isoformat(), 'episode_count': len(dedup), 'recent_count': len(recent), 'errors': errors, 'digest_path': str(DATA/'latest_digest.md')}
    if args.json:
        print(json.dumps({'result': result, 'recent': recent[:20]}, indent=2))
    else:
        print(json.dumps(result, indent=2))
        print('\n' + digest[:7000])

if __name__ == '__main__':
    main()
