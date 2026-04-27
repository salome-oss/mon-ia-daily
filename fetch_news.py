#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import socket
import time
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime


def _pick_tag(title: str) -> str:
    return "Meta" if "meta" in title.lower() else "IA"


def _format_fr_date(dt: datetime) -> str:
    months = [
        "janvier",
        "fevrier",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "aout",
        "septembre",
        "octobre",
        "novembre",
        "decembre",
    ]
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _fake_summary(tag: str, title: str, url: str, published: str | None) -> str:
    date = (published or datetime.now().strftime("%Y-%m-%d")).strip()
    hint = re.sub(r"\s+", " ", title).strip()
    hint = hint[:160] + ("…" if len(hint) > 160 else "")

    if tag == "Politique":
        angle = "Impacts possibles: conformité, transparence, gouvernance."
    elif tag == "Produit":
        angle = "À surveiller: adoption, cas d’usage, différenciation produit."
    else:
        angle = "Points clés attendus: méthode, performances, limites."

    return (
        f"[Résumé provisoire – {date}] {hint} "
        f"{angle} (Source: {urllib.parse.urlparse(url).netloc or 'lien'})."
    )


def _parse_published(entry) -> tuple[str | None, datetime | None]:
    if getattr(entry, "published_parsed", None):
        try:
            dt = datetime.fromtimestamp(time.mktime(entry.published_parsed))
            return _format_fr_date(dt), dt
        except Exception:
            pass

    published = getattr(entry, "published", None)
    if isinstance(published, str) and published.strip():
        raw = published.strip()
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return _format_fr_date(dt), dt
        except Exception:
            pass

    updated = getattr(entry, "updated", None)
    if isinstance(updated, str) and updated.strip():
        raw = updated.strip()
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return _format_fr_date(dt), dt
        except Exception:
            pass
    return None, None


def _rss_fetch(rss_url: str) -> list[dict]:
    try:
        import feedparser  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "La lib 'feedparser' n'est pas installée. Installe-la avec: pip install feedparser"
        ) from e

    feed = feedparser.parse(rss_url)
    if getattr(feed, "bozo", False):
        exc = getattr(feed, "bozo_exception", None)
        msg = str(exc) if exc else "Flux RSS invalide"
        raise RuntimeError(f"Erreur de lecture RSS: {msg}")

    entries = list(getattr(feed, "entries", []) or [])
    if not entries:
        raise RuntimeError("Aucune entrée RSS trouvée (feed vide).")
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch 50 AI news items via Google News RSS and overwrite news.json."
    )
    parser.add_argument(
        "--rss",
        default="https://news.google.com/rss/search?q=intelligence+artificielle+when:30d&hl=fr&gl=FR&ceid=FR:fr",
        help="URL RSS Google News à lire.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=50,
        help="Nombre d'articles à récupérer (défaut: 50).",
    )
    parser.add_argument(
        "--out",
        default="news.json",
        help="Chemin du fichier de sortie (défaut: news.json).",
    )
    args = parser.parse_args()

    socket.setdefaulttimeout(12.0)

    entries = _rss_fetch(args.rss)

    articles: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for entry in entries:
        if len(articles) >= max(1, args.count):
            break

        title = getattr(entry, "title", None) or "Article IA (titre indisponible)"
        url = getattr(entry, "link", None) or ""
        url = str(url).strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        tag = _pick_tag(title)
        published, published_dt = _parse_published(entry)
        if not published_dt:
            published_dt = datetime.now()
        if not published:
            published = _format_fr_date(published_dt)
        summary = _fake_summary(tag, title, url, published)
        articles.append(
            {
                "tag": tag,
                "title": title,
                "summary": summary,
                "url": url,
                "date": published or "",
                "_sort_ts": published_dt.timestamp() if published_dt else 0.0,
            }
        )

    articles.sort(key=lambda a: a.get("_sort_ts", 0.0), reverse=True)
    for article in articles:
        article.pop("_sort_ts", None)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"OK: {len(articles)} articles écrits dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

