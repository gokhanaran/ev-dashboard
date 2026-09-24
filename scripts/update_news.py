#!/usr/bin/env python3
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

FEEDS = [
    ("Habertürk", "https://www.haberturk.com/rss"),
    ("Sözcü", "https://www.sozcu.com.tr/feeds-son-dakika"),
    ("Sözcü", "https://www.sozcu.com.tr/feeds-haberler"),
    ("TRT Haber", "https://www.trthaber.com/sondakika_articles.rss"),
    ("Ensonhaber", "https://www.ensonhaber.com/rss/ensonhaber.xml"),
]

OUT = Path("news.json")
MAX_AGE_HOURS = 48
MAX_ITEMS = 120

def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; EvDashboardNewsBot/1.0; +https://github.com/gokhanaran/ev-dashboard)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()

def text_of(node, names):
    for name in names:
        el = node.find(name)
        if el is not None and el.text:
            return el.text.strip()
    return ""

def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def normalize_title(title):
    title = re.sub(r"\s+", " ", title or "").strip()
    return title

def title_key(title):
    t = title.casefold()
    t = re.sub(r"[^0-9a-zçğıöşü ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()

def parse_feed(source, raw):
    root = ET.fromstring(raw)
    items = []

    # RSS 2.0 / RDF style items.
    rss_items = root.findall(".//item")
    for item in rss_items:
        title = normalize_title(text_of(item, ["title"]))
        link = text_of(item, ["link", "guid"])
        pub = text_of(item, [
            "pubDate",
            "{http://purl.org/dc/elements/1.1/}date",
            "{http://purl.org/dc/terms/}issued",
        ])
        dt = parse_date(pub)
        if title and dt:
            items.append({
                "title": title,
                "link": link,
                "source": source,
                "published_at": dt.isoformat().replace("+00:00", "Z"),
            })

    # Atom fallback.
    if not items:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns):
            title = normalize_title(text_of(entry, ["{http://www.w3.org/2005/Atom}title"]))
            pub = text_of(entry, [
                "{http://www.w3.org/2005/Atom}published",
                "{http://www.w3.org/2005/Atom}updated",
            ])
            dt = parse_date(pub)
            link = ""
            link_el = entry.find("{http://www.w3.org/2005/Atom}link")
            if link_el is not None:
                link = link_el.attrib.get("href", "")
            if title and dt:
                items.append({
                    "title": title,
                    "link": link,
                    "source": source,
                    "published_at": dt.isoformat().replace("+00:00", "Z"),
                })
    return items

def load_previous():
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return []

def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    collected = []
    statuses = []

    for source, url in FEEDS:
        try:
            raw = fetch(url)
            items = parse_feed(source, raw)
            collected.extend(items)
            statuses.append({"source": source, "url": url, "ok": True, "items": len(items)})
            print(f"OK  {source}: {len(items)}")
        except Exception as e:
            statuses.append({"source": source, "url": url, "ok": False, "error": str(e)})
            print(f"ERR {source}: {e}", file=sys.stderr)

    # Keep recent previously collected items too, so one temporarily failing feed
    # does not make the dashboard suddenly empty.
    collected.extend(load_previous())

    clean = []
    seen = set()
    for item in collected:
        dt = parse_date(item.get("published_at", ""))
        if not dt or dt < cutoff or dt > now + timedelta(minutes=10):
            continue
        title = normalize_title(item.get("title", ""))
        if not title:
            continue
        key = title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        clean.append({
            "title": title,
            "link": item.get("link", ""),
            "source": item.get("source", ""),
            "published_at": dt.isoformat().replace("+00:00", "Z"),
        })

    clean.sort(key=lambda x: x["published_at"], reverse=True)
    clean = clean[:MAX_ITEMS]

    old = None
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Avoid a commit every five minutes when the actual news list has not changed.
    if old and old.get("items") == clean:
        print(f"No news changes. Keeping existing news.json ({len(clean)} items).")
        return

    payload = {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window_hours": MAX_AGE_HOURS,
        "count": len(clean),
        "sources": statuses,
        "items": clean,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(clean)} items to {OUT}")

if __name__ == "__main__":
    main()
