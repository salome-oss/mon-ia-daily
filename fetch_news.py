from __future__ import annotations

import json
import unicodedata
from html.parser import HTMLParser
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

try:
    import feedparser
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise SystemExit(
        "Le module 'feedparser' est requis. Installe-le avec: pip install feedparser"
    ) from exc


# Multi-feeds to make the site a worldwide press reference.
# We intentionally query by major zones so every article can have a continent,
# even when the media is not in the bias database.
FEEDS = [
    {
        "name": "Europe (FR)",
        "continent": "Europe",
        "url": "https://news.google.com/rss/search?q=intelligence+artificielle&hl=fr&gl=FR&ceid=FR:fr",
    },
    {
        "name": "Amerique du Nord (US)",
        "continent": "Amerique du Nord",
        "url": "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en",
    },
    {
        "name": "Amerique du Sud (BR)",
        "continent": "Amerique du Sud",
        "url": "https://news.google.com/rss/search?q=intelig%C3%AAncia+artificial&hl=pt-BR&gl=BR&ceid=BR:pt-419",
    },
    {
        "name": "Afrique (ZA)",
        "continent": "Afrique",
        "url": "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-ZA&gl=ZA&ceid=ZA:en",
    },
    {
        "name": "Asie (SG)",
        "continent": "Asie",
        "url": "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-SG&gl=SG&ceid=SG:en",
    },
    {
        "name": "Oceanie (AU)",
        "continent": "Oceanie",
        "url": "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-AU&gl=AU&ceid=AU:en",
    },
]
NEWS_FILE = Path(__file__).with_name("news.json")
MEDIA_BIAS_FILE = Path(__file__).with_name("media_bias.json")
TRANSLATIONS_CACHE_FILE = Path(__file__).with_name("translations_cache.json")
RETENTION_DAYS = 30
DEFAULT_LEANING = "A verifier"
DEFAULT_OWNER = "Non renseigne"
DEFAULT_CONTINENT = "Inconnu"
TRANSLATE_ENDPOINT = "https://libretranslate.com/translate"
TRANSLATE_TIMEOUT_S = 12
MAX_TRANSLATIONS_PER_RUN = 40
MAX_TRANSLATABLE_CHARS = 900


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def strip_html(value: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(value or "")
    return stripper.get_text().strip()


def detect_is_french(text: str) -> bool:
    t = normalize_text(text)
    if not t:
        return True
    french_markers = [
        " le ",
        " la ",
        " les ",
        " des ",
        " une ",
        " un ",
        " et ",
        " est ",
        " dans ",
        " sur ",
        " avec ",
        " pour ",
        " que ",
        " au ",
        " aux ",
    ]
    score = sum(1 for m in french_markers if m.strip() in t.split())
    return score >= 3


def translate_to_french(text: str) -> str:
    if not text:
        return ""

    payload = json.dumps(
        {
            "q": text,
            "source": "auto",
            "target": "fr",
            "format": "text",
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = Request(
        TRANSLATE_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=TRANSLATE_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        translated = str(data.get("translatedText", "")).strip()
        return translated or text
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return text


def load_translation_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    cache: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and k and v:
            cache[k] = v
    return cache


def save_translation_cache(path: Path, cache: dict[str, str]) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def translation_cache_key(title: str, summary: str) -> str:
    # Stable key without extra deps: title + first 250 chars of summary.
    base = (title.strip() + "\n" + summary.strip()[:250]).strip()
    return base

US_MEDIA_NAMES = {
    "The New York Times",
    "The Washington Post",
    "The Wall Street Journal",
    "Reuters",
    "Associated Press (AP)",
    "Bloomberg",
    "POLITICO",
    "The Hill",
    "Axios",
    "CNN",
    "MSNBC",
    "Fox News",
    "Breitbart",
    "The Daily Wire",
    "NPR",
    "PBS",
    "TechCrunch",
    "The Verge",
    "WIRED",
    "The Information",
}

CHINA_MEDIA_NAMES = {
    "Xinhua",
    "People's Daily",
    "China Daily",
    "CGTN",
    "Global Times",
}


def load_existing_news(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(data, list):
        return []

    normalized_items: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        published_at = str(item.get("published_at", "")).strip()
        category = categorize_title(title)
        source = extract_source_name(title)
        media_info = lookup_media_metadata(source)
        summary = str(item.get("summary", "")).strip()
        summary_fr = str(item.get("summary_fr", "")).strip() or summary
        lang = str(item.get("lang", "")).strip() or ("fr" if detect_is_french(f"{title}. {summary_fr}") else "non-fr")

        if not title or not url:
            continue

        if parse_iso_datetime(published_at) is None:
            continue

        normalized_items.append(
            {
                "title": title,
                "url": url,
                "published_at": published_at,
                "category": category,
                "summary": summary,
                "summary_fr": summary_fr,
                "lang": lang,
                "source": media_info["media"],
                "owner": media_info["owner"],
                "political_leaning": media_info["political_leaning"],
                "continent": media_info["continent"],
            }
        )

    return normalized_items


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def parse_entry_datetime(entry: Any) -> datetime:
    candidates = [
        getattr(entry, "published", None),
        getattr(entry, "updated", None),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)


def normalize_url(entry: Any) -> str:
    for key in ("link", "id"):
        value = str(getattr(entry, key, "") or "").strip()
        if value:
            return value
    return ""


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_text.lower().replace("’", "'").replace("-", " ").split())


def load_media_bias_database(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    database: dict[str, dict[str, str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue

        media = str(value.get("media", "")).strip()
        owner = str(value.get("owner", "")).strip() or DEFAULT_OWNER
        political_leaning = str(value.get("political_leaning", "")).strip() or DEFAULT_LEANING
        continent = str(value.get("continent", "")).strip() or DEFAULT_CONTINENT

        if not media:
            continue

        database[normalize_text(key)] = {
            "media": media,
            "owner": owner,
            "political_leaning": political_leaning,
            "continent": continent,
        }

    return database


def extract_source_name(title: str) -> str:
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    return "Source inconnue"


MEDIA_BIAS_DATABASE = load_media_bias_database(MEDIA_BIAS_FILE)

def infer_continent(media: str, owner: str) -> str:
    owner_norm = normalize_text(owner)

    if "chine" in owner_norm or media in CHINA_MEDIA_NAMES:
        return "Asie"

    if media in US_MEDIA_NAMES:
        return "Amerique du Nord"

    return DEFAULT_CONTINENT


def lookup_media_metadata(source: str, *, fallback_continent: str | None = None) -> dict[str, str]:
    normalized_source = normalize_text(source)
    if normalized_source in MEDIA_BIAS_DATABASE:
        meta = dict(MEDIA_BIAS_DATABASE[normalized_source])
        current_continent = (meta.get("continent") or "").strip()
        if not current_continent or current_continent == DEFAULT_CONTINENT:
            meta["continent"] = infer_continent(meta["media"], meta["owner"]) or (fallback_continent or DEFAULT_CONTINENT)
        return meta

    for key, value in MEDIA_BIAS_DATABASE.items():
        if key in normalized_source or normalized_source in key:
            meta = dict(value)
            current_continent = (meta.get("continent") or "").strip()
            if not current_continent or current_continent == DEFAULT_CONTINENT:
                meta["continent"] = infer_continent(meta["media"], meta["owner"]) or (fallback_continent or DEFAULT_CONTINENT)
            return meta

    return {
        "media": source if source and source != "Source inconnue" else "Source inconnue",
        "owner": DEFAULT_OWNER,
        "political_leaning": DEFAULT_LEANING,
        "continent": fallback_continent or DEFAULT_CONTINENT,
    }


def categorize_title(title: str) -> str:
    lower_title = title.lower()

    mapping = {
        "Politique": [
            "gouvernement",
            "etat",
            "union europeenne",
            "commission",
            "regulation",
            "reglementation",
            "loi",
            "ministere",
            "parlement",
            "senat",
            "politique",
            "souverainete",
            "souveraineté",
            "diplomatie",
            "geopolitique",
            "géopolitique",
        ],
        "Economie": [
            "levee de fonds",
            "levée de fonds",
            "startup",
            "entreprise",
            "marche",
            "marché",
            "finance",
            "investissement",
            "business",
            "revenu",
            "profit",
            "bourse",
            "clients",
            "economie",
            "économie",
            "croissance",
            "productivite",
            "productivité",
            "industrie",
            "emploi",
            "cout",
            "coût",
            "monetisation",
            "monétisation",
        ],
        "Communication": [
            "media",
            "média",
            "presse",
            "journalisme",
            "journaliste",
            "publicite",
            "publicité",
            "marketing",
            "reseaux sociaux",
            "réseaux sociaux",
            "voix",
            "musique",
            "sacem",
            "communication",
            "contenu",
            "image",
            "video",
            "vidéo",
            "deepfake",
            "desinformation",
            "désinformation",
            "marque",
        ],
        "Mise a jour": [
            "modele",
            "modèle",
            "llm",
            "ia generative",
            "ia générative",
            "agent",
            "robot",
            "puce",
            "gpu",
            "openai",
            "google",
            "mistral",
            "anthropic",
            "nvidia",
            "tech",
            "mise a jour",
            "mise à jour",
            "nouveau",
            "nouvelle version",
            "lance",
            "lancement",
            "devoile",
            "dévoile",
            "annonce",
            "update",
            "sort",
            "publie",
            "déploie",
            "deploie",
        ],
    }

    for category, keywords in mapping.items():
        if any(keyword in lower_title for keyword in keywords):
            return category

    return "Mise a jour"


def fetch_feed_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    translation_cache = load_translation_cache(TRANSLATIONS_CACHE_FILE)
    translations_used = 0

    for feed in FEEDS:
        parsed = feedparser.parse(feed["url"])

        if getattr(parsed, "bozo", 0):
            exception = getattr(parsed, "bozo_exception", None)
            if exception and not getattr(parsed, "entries", []):
                raise RuntimeError(f"Impossible de lire le flux RSS ({feed['name']}): {exception}") from exception

        for entry in parsed.entries:
            title = str(getattr(entry, "title", "") or "").strip()
            url = normalize_url(entry)
            published_at = parse_entry_datetime(entry).isoformat()
            raw_summary = str(getattr(entry, "summary", "") or getattr(entry, "description", "") or "").strip()
            summary = strip_html(raw_summary)

            if not title or not url:
                continue

            media_info = lookup_media_metadata(extract_source_name(title), fallback_continent=feed["continent"])
            is_fr = detect_is_french(f"{title}. {summary}")
            summary_fr = summary
            if summary and not is_fr:
                cache_key = translation_cache_key(title, summary)
                if cache_key in translation_cache:
                    summary_fr = translation_cache[cache_key]
                elif translations_used < MAX_TRANSLATIONS_PER_RUN and len(summary) <= MAX_TRANSLATABLE_CHARS:
                    translated = translate_to_french(summary)
                    summary_fr = translated
                    translation_cache[cache_key] = translated
                    translations_used += 1
            entries.append(
                {
                    "title": title,
                    "url": url,
                    "published_at": published_at,
                    "category": categorize_title(title),
                    "summary": summary,
                    "summary_fr": summary_fr,
                    "lang": "fr" if is_fr else "non-fr",
                    "source": media_info["media"],
                    "owner": media_info["owner"],
                    "political_leaning": media_info["political_leaning"],
                    "continent": media_info["continent"],
                    "feed": feed["name"],
                }
            )

    if translations_used > 0:
        save_translation_cache(TRANSLATIONS_CACHE_FILE, translation_cache)

    return entries


def deduplicate_and_merge(
    existing_items: list[dict[str, Any]], new_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()

    # Prefer newly fetched items (usually richer metadata like continent/source).
    for item in new_items + existing_items:
        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()

        if not title or not url:
            continue

        url_key = url.casefold()
        title_key = title.casefold()

        if url_key in seen_urls or title_key in seen_titles:
            continue

        seen_urls.add(url_key)
        seen_titles.add(title_key)
        merged.append(item)

    return merged


def prune_old_items(items: list[dict[str, Any]], retention_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    fresh_items: list[dict[str, Any]] = []

    for item in items:
        published_at = parse_iso_datetime(str(item.get("published_at", "")).strip())
        if published_at is None:
            continue
        if published_at >= cutoff:
            fresh_items.append(item)

    return fresh_items


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: parse_iso_datetime(str(item.get("published_at", ""))) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def save_news(path: Path, items: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    existing_items = load_existing_news(NEWS_FILE)
    fetched_items = fetch_feed_entries()
    merged_items = deduplicate_and_merge(existing_items, fetched_items)
    fresh_items = prune_old_items(merged_items, RETENTION_DAYS)
    sorted_items = sort_items(fresh_items)
    save_news(NEWS_FILE, sorted_items)

    print(f"{len(fetched_items)} articles lus depuis le flux RSS.")
    print(f"{len(sorted_items)} articles conservés dans {NEWS_FILE.name}.")


if __name__ == "__main__":
    main()
