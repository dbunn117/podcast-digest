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
import shutil
import subprocess
import tempfile
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
TRANSCRIPTS = DATA / 'transcripts'
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
    'Modern Wisdom': {'accent': '#a855f7', 'gradient': ['#3b0764', '#c084fc'], 'emoji': '🧠'},
    'The Game': {'accent': '#f59e0b', 'gradient': ['#451a03', '#fbbf24'], 'emoji': '💰'},
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




def split_sentences(text: str) -> list[str]:
    text = re.sub(r'\s+', ' ', strip_html(text)).strip()
    if not text:
        return []
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]


def score_sentence(sentence: str, tags=None) -> int:
    tags = set(tags or [])
    s = sentence.lower()
    score = 0
    for word in ['because', 'why', 'how', 'means', 'strategy', 'risk', 'opportunity', 'workflow', 'system', 'operator', 'business', 'customer', 'revenue', 'margin', 'cost', 'market', 'agent', 'automation', 'model', 'data']:
        if word in s:
            score += 2
    if tags & {'AI', 'Startups'} and re.search(r'\b(ai|agent|model|automation|workflow|software|startup|product)\b', s):
        score += 4
    if tags & {'Finance', 'Markets'} and re.search(r'\b(market|capital|rate|debt|margin|cash|valuation|investor|risk)\b', s):
        score += 4
    if tags & {'Business', 'Entrepreneurship'} and re.search(r'\b(customer|revenue|pricing|sales|distribution|offer|growth|profit)\b', s):
        score += 4
    if 90 <= len(sentence) <= 240:
        score += 2
    if re.search(r'(?i)subscribe|sponsor|promo code|advertis|follow us|check out', sentence):
        score -= 8
    return score


def extract_takeaways_from_text(text: str, tags=None, limit=4) -> list[str]:
    sentences = [s for s in split_sentences(text) if 55 <= len(s) <= 280]
    ranked = sorted(sentences, key=lambda x: score_sentence(x, tags), reverse=True)
    picked: list[str] = []
    seen = set()
    for sentence in ranked:
        key = re.sub(r'[^a-z0-9 ]', '', sentence.lower())[:80]
        if key in seen:
            continue
        if any(sentence[:45] in existing or existing[:45] in sentence for existing in picked):
            continue
        picked.append(sentence)
        seen.add(key)
        if len(picked) >= limit:
            break
    return picked


def transcript_paths(episode: dict) -> tuple[Path, Path]:
    safe = re.sub(r'[^a-zA-Z0-9_.-]+', '-', f"{episode.get('published_date') or 'undated'}-{episode.get('short_name','podcast')}-{episode.get('id')}")[:160]
    return TRANSCRIPTS / f'{safe}.json', TRANSCRIPTS / f'{safe}.txt'


def load_cached_transcript(episode: dict) -> dict | None:
    json_path, _ = transcript_paths(episode)
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if data.get('audio_url') and episode.get('audio_url') and data.get('audio_url') != episode.get('audio_url'):
        return None
    return data


def attach_transcript(episode: dict, transcript_data: dict | None):
    if not transcript_data:
        episode['transcript_status'] = episode.get('transcript_status') or 'not_ingested'
        return
    text = transcript_data.get('transcript', '').strip()
    if not text:
        episode['transcript_status'] = transcript_data.get('status') or 'empty'
        return
    takeaways = transcript_data.get('llm_takeaways') or transcript_data.get('takeaways') or extract_takeaways_from_text(text, episode.get('tags'), limit=4)
    episode['transcript_status'] = 'available'
    episode['transcript_source'] = transcript_data.get('source', 'faster_whisper')
    episode['transcript_model'] = transcript_data.get('model', '')
    episode['transcript_generated_at'] = transcript_data.get('generated_at', '')
    episode['transcript_takeaways'] = takeaways[:4]
    if transcript_data.get('llm_summary'):
        episode['transcript_summary'] = transcript_data['llm_summary'][:900]
    elif transcript_data.get('llm_takeaways'):
        episode['transcript_summary'] = ' '.join(transcript_data['llm_takeaways'][:2])[:900]
    else:
        summary_bits = extract_takeaways_from_text(text, episode.get('tags'), limit=3)
        episode['transcript_summary'] = ' '.join(summary_bits)[:900]




def parse_bullets(text: str) -> list[str]:
    bullets = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r'^[-*•]\s+', '', line)
        line = re.sub(r'^\d+[.)]\s+', '', line)
        if 30 <= len(line) <= 500:
            bullets.append(line)
    return bullets[:5]


def generate_llm_takeaways(transcript: str, episode: dict, timeout: int = 240) -> list[str]:
    excerpt = transcript[:14000]
    prompt = (
        "From this podcast transcript excerpt, write 4 concise, specific key takeaways for David Bunn. "
        "Focus on practical AI consulting/business implications, finance/operator lessons, health/family relevance only if clearly present, and concrete actions. "
        "Do not quote filler or generic episode marketing copy. Return bullet list only.\n\n"
        f"Podcast: {episode.get('short_name')}\nTitle: {episode.get('title')}\nTags: {', '.join(episode.get('tags') or [])}\n\nTranscript:\n{excerpt}"
    )
    try:
        cp = subprocess.run(
            ['hermes', '--ignore-rules', '-z', prompt],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except Exception:
        return []
    if cp.returncode != 0:
        return []
    return parse_bullets(cp.stdout)


def transcribe_episode(episode: dict, model_size: str, max_minutes: int, force: bool = False) -> dict:
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    json_path, txt_path = transcript_paths(episode)
    if not force:
        cached = load_cached_transcript(episode)
        if cached:
            return cached
    audio_url = episode.get('audio_url')
    if not audio_url:
        data = {'status': 'no_audio_url', 'transcript': '', 'episode_id': episode.get('id'), 'title': episode.get('title')}
        json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data
    if shutil.which('ffmpeg') is None:
        data = {'status': 'missing_ffmpeg', 'transcript': '', 'episode_id': episode.get('id'), 'title': episode.get('title')}
        json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        data = {'status': f'missing_faster_whisper: {exc}', 'transcript': '', 'episode_id': episode.get('id'), 'title': episode.get('title')}
        json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        return data
    with tempfile.TemporaryDirectory(prefix='podcast-transcript-') as td:
        wav = Path(td) / 'audio.wav'
        cmd = [
            'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-i', audio_url,
            '-t', str(max_minutes * 60),
            '-vn', '-ac', '1', '-ar', '16000', str(wav),
        ]
        subprocess.run(cmd, check=True, timeout=max(180, max_minutes * 90))
        model = WhisperModel(model_size, device='cpu', compute_type='int8')
        segments, info = model.transcribe(str(wav), beam_size=1, vad_filter=True)
        segs = []
        parts = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            segs.append({'start': round(seg.start, 2), 'end': round(seg.end, 2), 'text': text})
            parts.append(text)
    transcript = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
    data = {
        'status': 'available' if transcript else 'empty',
        'source': 'faster_whisper',
        'model': model_size,
        'max_minutes': max_minutes,
        'language': getattr(info, 'language', None),
        'duration': getattr(info, 'duration', None),
        'generated_at': datetime.now(PACIFIC).isoformat(),
        'episode_id': episode.get('id'),
        'podcast': episode.get('short_name'),
        'title': episode.get('title'),
        'published_date': episode.get('published_date'),
        'audio_url': audio_url,
        'transcript': transcript,
        'takeaways': extract_takeaways_from_text(transcript, episode.get('tags'), limit=4),
        'segments': segs,
    }
    json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    txt_path.write_text(transcript + '\n', encoding='utf-8')
    return data


def attach_cached_transcripts(episodes: list[dict]):
    for episode in episodes:
        attach_transcript(episode, load_cached_transcript(episode))


def transcribe_recent_episodes(episodes: list[dict], limit: int, model_size: str, max_minutes: int, force: bool = False, llm_takeaways: bool = False) -> list[dict]:
    if limit <= 0:
        attach_cached_transcripts(episodes)
        return []
    candidates = [e for e in sorted(episodes, key=episode_sort_key, reverse=True) if e.get('audio_url') and e.get('source') == 'favorite_feed']
    completed = []
    for episode in candidates[:limit]:
        try:
            data = transcribe_episode(episode, model_size=model_size, max_minutes=max_minutes, force=force)
        except Exception as exc:
            data = {'status': f'error: {type(exc).__name__}: {exc}', 'transcript': '', 'episode_id': episode.get('id'), 'title': episode.get('title')}
            json_path, _ = transcript_paths(episode)
            TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        if llm_takeaways and data.get('transcript') and (force or not data.get('llm_takeaways')):
            bullets = generate_llm_takeaways(data['transcript'], episode)
            if bullets:
                data['llm_takeaways'] = bullets
                data['takeaways'] = bullets
                json_path, _ = transcript_paths(episode)
                json_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        elif data.get('llm_takeaways'):
            data['takeaways'] = data['llm_takeaways']
        attach_transcript(episode, data)
        completed.append({'podcast': episode.get('short_name'), 'title': episode.get('title'), 'status': data.get('status'), 'chars': len(data.get('transcript', '')), 'llm_takeaways': bool(data.get('llm_takeaways'))})
    attach_cached_transcripts(episodes)
    return completed


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
    template = ROOT / 'templates' / 'index.html'
    html_doc = template.read_text(encoding='utf-8')
    (DOCS / 'index.html').write_text(html_doc, encoding='utf-8')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=2, help='Recent digest window')
    ap.add_argument('--since', default='2026-01-01', help='Dashboard history start date, YYYY-MM-DD')
    ap.add_argument('--write', action='store_true', help='Write Obsidian daily digest')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--transcribe-recent', type=int, default=0, help='Transcribe N newest audio episodes before writing dashboard')
    ap.add_argument('--transcript-model', default='tiny.en', help='faster-whisper model size for transcript ingestion')
    ap.add_argument('--transcript-max-minutes', type=int, default=45, help='Max minutes to transcribe per episode')
    ap.add_argument('--force-transcripts', action='store_true', help='Regenerate cached transcripts for selected recent episodes')
    ap.add_argument('--llm-transcript-takeaways', action='store_true', help='Use Hermes oneshot to turn transcripts into David-specific takeaways')
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

    transcript_runs = transcribe_recent_episodes(
        dedup,
        limit=args.transcribe_recent,
        model_size=args.transcript_model,
        max_minutes=args.transcript_max_minutes,
        force=args.force_transcripts,
        llm_takeaways=args.llm_transcript_takeaways,
    )
    ytd = filter_since(dedup, since)
    digest, recent = build_digest(dedup, args.days, config)
    stats = build_stats(dedup, ytd, recent, podcasts)
    if transcript_runs:
        stats['transcript_runs'] = transcript_runs

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
        'transcript_runs': transcript_runs,
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
