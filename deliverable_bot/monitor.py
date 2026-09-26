#!/usr/bin/env python3
"""Monitor annunci residenziali in affitto a Pesaro e notifica via Telegram."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag


STATE_FILE = Path(os.getenv("STATE_FILE", "state/seen.json"))
TIMEOUT = (8, 15)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
}

HOME_WORDS = re.compile(
    r"appartament|bilocal|trilocal|quadrilocal|monolocal|attic|mansard|villa|"
    r"villett|schiera|bifamiliar|casa|porzione|loft|residenzial|camera|stanza",
    re.I,
)
COMMERCIAL_WORDS = re.compile(
    r"ufficio|negozio|commercial|capannone|magazzino|laboratorio|terreno|garage|"
    r"posto auto|parrucchier|attivit|azienda",
    re.I,
)
PRICE_RE = re.compile(r"(?:€\s*|EUR\s*)([\d.]+(?:,\d{2})?)|([\d.]+(?:,\d{2})?)\s*€", re.I)


@dataclass(frozen=True)
class Site:
    name: str
    url: str
    path_pattern: str
    sitemap_urls: tuple[str, ...] = ()
    assume_residential: bool = False


SITES = (
    Site(
        "Kimia Home Immobiliare",
        "https://kimiahomeimmobiliare.it/it/affitti/",
        r"/it/affitti/[^/]+",
        ("https://kimiahomeimmobiliare.it/wp-sitemap.xml",),
    ),
    Site(
        "Agenzia Holiday Home",
        "https://www.agenziaholidayhome.it/ricerca-immobile/agenzia-affitti-pesaro/",
        r"/property/[^/]+",
        ("https://www.agenziaholidayhome.it/wp-sitemap.xml",),
    ),
    Site(
        "MT Casa",
        "https://www.mt-casa.it/it/affitti-residenziali/",
        r"/it/affitti/[^/]+",
        ("https://www.mt-casa.it/sitemap.xml",),
        True,
    ),
    Site(
        "Immobiliare Trieste Pesaro",
        "https://immobiliaretriestepesaro.it/it/affitti-residenziali/",
        r"/it/(?:affitti-residenziali|affitti)/[^/]+",
        ("https://immobiliaretriestepesaro.it/sitemap.xml",),
        True,
    ),
    Site(
        "Falcioni Immobiliare",
        "https://www.falcionimmobiliare.com/immobili-in-affitto",
        r"/scheda-immobile/\d+/[^/]+",
    ),
    Site(
        "Pesaro Case",
        "https://www.pesarocase.com/affitta-immobile-pesaro/",
        r"/immobile/[^/]+",
        ("https://www.pesarocase.com/sitemap.xml",),
    ),
    Site(
        "Andreani Casa",
        "https://www.andreanicasa.it/immobili-fitto/",
        r"/immobile/[^/]+",
    ),
    Site(
        "Bicasa",
        "https://www.bicasa.net/immobili-fitto/",
        r"/immobile/[^/]+",
    ),
    Site(
        "Immobiliare.it",
        "https://www.immobiliare.it/affitto-case/pesaro/",
        r"/annunci/\d+",
        assume_residential=True,
    ),
    Site(
        "Casa.it",
        "https://www.casa.it/affitto/residenziale/pesaro/",
        r"/immobili/\d+",
        assume_residential=True,
    ),
    Site(
        "Idealista",
        "https://www.idealista.it/affitto-case/pesaro-pesaro-urbino/",
        r"/immobile/\d+",
        assume_residential=True,
    ),
)


def canonical_url(raw: str) -> str:
    parts = urlsplit(raw)
    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def key_for(site: Site, url: str) -> str:
    return hashlib.sha256(f"{site.name}|{canonical_url(url)}".encode()).hexdigest()[:24]


def get(url: str) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 1:
                time.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def surrounding_text(anchor: Tag) -> str:
    node: Tag | None = anchor
    best = anchor.get_text(" ", strip=True)
    for _ in range(5):
        if node is None or not isinstance(node.parent, Tag):
            break
        node = node.parent
        candidate = node.get_text(" ", strip=True)
        if len(candidate) <= 700:
            best = candidate
        else:
            break
    return re.sub(r"\s+", " ", best).strip()


def is_home(site: Site, url: str, text: str) -> bool:
    combined = f"{url} {text}"
    if COMMERCIAL_WORDS.search(combined):
        return False
    return site.assume_residential or bool(HOME_WORDS.search(combined))


def listing_from(site: Site, url: str, text: str, title_hint: str = "") -> dict[str, str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    price_match = PRICE_RE.search(cleaned)
    price = price_match.group(0) if price_match else "Prezzo non indicato"
    title = title_hint or cleaned
    if not title or len(title) > 160:
        slug = urlsplit(url).path.rstrip("/").split("/")[-1]
        title = slug.replace("-", " ").strip().title()
    return {"id": key_for(site, url), "url": canonical_url(url), "title": title[:180], "price": price}


def extract_page(site: Site, body: str, base_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(body, "html.parser")
    found: dict[str, dict[str, str]] = {}
    quality: dict[str, int] = {}
    pattern = re.compile(site.path_pattern, re.I)
    for anchor in soup.select("a[href]"):
        url = canonical_url(urljoin(base_url, str(anchor.get("href"))))
        if not pattern.search(urlsplit(url).path):
            continue
        anchor_title = str(anchor.get("title") or "").strip()
        text = " ".join(part for part in (anchor_title, surrounding_text(anchor)) if part)
        if is_home(site, url, text):
            item = listing_from(site, url, text, anchor_title)
            item_score = bool(anchor_title) * 5000 + (item["price"] != "Prezzo non indicato") * 1000 + len(item["title"])
            if item_score > quality.get(item["id"], -1):
                found[item["id"]] = item
                quality[item["id"]] = item_score
    return list(found.values())


def sitemap_locations(body: str) -> Iterable[str]:
    soup = BeautifulSoup(body, "xml")
    for loc in soup.find_all("loc"):
        if loc.text.strip():
            yield loc.text.strip()


def extract_sitemaps(site: Site) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    queue = list(site.sitemap_urls)
    visited: set[str] = set()
    pattern = re.compile(site.path_pattern, re.I)
    while queue and len(visited) < 12:
        sitemap = queue.pop(0)
        if sitemap in visited:
            continue
        visited.add(sitemap)
        body = get(sitemap).text
        for loc in sitemap_locations(body):
            if loc.endswith(".xml"):
                queue.append(loc)
                continue
            url = canonical_url(loc)
            if pattern.search(urlsplit(url).path) and is_home(site, url, url):
                item = listing_from(site, url, "")
                found[item["id"]] = item
    return list(found.values())


def scan(site: Site) -> list[dict[str, str]]:
    errors: list[str] = []
    found: dict[str, dict[str, str]] = {}
    try:
        response = get(site.url)
        for item in extract_page(site, response.text, response.url):
            found[item["id"]] = item
    except Exception as exc:  # continua con la sitemap
        errors.append(f"pagina: {exc}")
    if site.sitemap_urls:
        try:
            for item in extract_sitemaps(site):
                found[item["id"]] = item
        except Exception as exc:
            errors.append(f"sitemap: {exc}")
    if not found and errors:
        raise RuntimeError("; ".join(errors))
    return list(found.values())


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"initialized": False, "seen": {}}
    try:
        saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        # Mantiene soltanto i dati necessari a riconoscere gli annunci già visti.
        # I vecchi contatori degli errori causavano un commit GitHub quasi ogni 5 minuti.
        return {
            "initialized": bool(saved.get("initialized", False)),
            "seen": saved.get("seen", {}),
        }
    except (json.JSONDecodeError, OSError):
        return {"initialized": False, "seen": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("Mancano TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID")
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML", "disable_web_page_preview": False},
        timeout=TIMEOUT,
    )
    response.raise_for_status()


def notify_listing(site: Site, item: dict[str, str]) -> None:
    telegram(
        "🏠 <b>Nuovo affitto trovato</b>\n\n"
        f"<b>Agenzia:</b> {html.escape(site.name)}\n"
        f"<b>Annuncio:</b> {html.escape(item['title'])}\n"
        f"<b>Prezzo:</b> {html.escape(item['price'])}\n\n"
        f"<a href=\"{html.escape(item['url'], quote=True)}\">Apri subito l'annuncio</a>"
    )


def run(dry_run: bool = False) -> int:
    state = load_state()
    notify_first = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() == "true"
    total = 0
    new_count = 0
    for site in SITES:
        site_first_run = site.name not in state.setdefault("seen", {})
        try:
            items = scan(site)
            total += len(items)
            previous = set(state.setdefault("seen", {}).get(site.name, []))
            new_items = [item for item in items if item["id"] not in previous]
            if (not site_first_run or notify_first) and not dry_run:
                for item in new_items:
                    notify_listing(site, item)
                    new_count += 1
            state["seen"][site.name] = sorted(previous | {item["id"] for item in items})[-1000:]
            print(f"OK  {site.name}: {len(items)} annunci ({len(new_items)} nuovi)")
        except Exception as exc:
            print(f"ERR {site.name}: {exc}", file=sys.stderr)
    state["initialized"] = True
    save_state(state)
    print(f"Totale: {total} annunci; notifiche inviate: {new_count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="controlla senza inviare notifiche")
    parser.add_argument("--test-telegram", action="store_true", help="invia un messaggio Telegram di prova")
    args = parser.parse_args()
    if args.test_telegram:
        telegram("✅ <b>Bot affitti Pesaro collegato!</b>\nRiceverai qui i nuovi annunci.")
        return 0
    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
