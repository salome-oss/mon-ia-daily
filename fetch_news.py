#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import socket
import time
import urllib.parse
from datetime import datetime


def _pick_tag(title: str, url: str) -> str:
    s = f"{title} {url}".lower()
    if any(k in s for k in ("regulation", "regulatory", "policy", "government", "commission", "senate", "parliament", "loi", "réglement", "gouvernement", "union européenne", "european")):
        return "Politique"
    if any(k in s for k in ("product", "launch", "released", "feature", "app", "outil", "produit", "plateforme", "startup", "lance", "sortie", "beta")):
        return "Produit"
    return "Technique"


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


def _dedupe_keep_order(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _parse_published(entry) -> str | None:
    if getattr(entry, "published_parsed", None):
        try:
            return time.strftime("%Y-%m-%d", entry.published_parsed)
        except Exception:
            pass
    published = getattr(entry, "published", None)
    if isinstance(published, str) and published.strip():
        return published.strip()
    updated = getattr(entry, "updated", None)
    if isinstance(updated, str) and updated.strip():
        return updated.strip()
    return None


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
        description="Fetch 5 AI news items via Google News RSS and overwrite news.json."
    )
    parser.add_argument(
        "--rss",
        default="https://news.google.com/rss/search?q=intelligence+artificielle+2026&hl=fr&gl=FR&ceid=FR:fr",
        help="URL RSS Google News à lire.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="Nombre d'articles à récupérer (défaut: 5).",
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
    urls_seen: list[str] = []
    for entry in entries:
        if len(articles) >= max(1, args.count):
            break

        title = getattr(entry, "title", None) or "Article IA (titre indisponible)"
        url = getattr(entry, "link", None) or ""
        url = str(url).strip()
        if not url:
            continue
        urls_seen.append(url)
        if url in _dedupe_keep_order(urls_seen)[:-1]:
            continue

        tag = _pick_tag(title, url)
        published = _parse_published(entry)
        summary = _fake_summary(tag, title, url, published)
        articles.append(
            {
                "tag": tag,
                "title": title,
                "summary": summary,
                "url": url,
            }
        )

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"OK: {len(articles)} articles écrits dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

