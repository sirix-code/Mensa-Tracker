"""
Schickt morgens den Speiseplan der Mensa Blechhammer (Hochschule Schmalkalden) per Telegram.

Quelle: Speiseplan-Seite des Studierendenwerks Thüringen (offizielle Seite, kein Bot-Schutz erwartet).

Umgebungsvariablen:
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   -> dieselben wie beim Tracker
    MENSA_WATCH                        -> optional, Stichwörter mit Komma, z.B. "schnitzel,burger,pizza"
                                          Passende Gerichte werden mit ⭐ markiert.

Test ohne Telegram:  python mensa.py --dry

Eigenständig: braucht keine andere Datei aus dem Repo (pip install requests beautifulsoup4).
"""

import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def get_retry(session, url, **kwargs):
    """GET mit einem zweiten Versuch nach kurzer Pause bei 403/429."""
    r = session.get(url, **kwargs)
    if r.status_code in (403, 429):
        time.sleep(random.uniform(8, 12))
        r = session.get(url, **kwargs)
    return r


def notify(text):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text)
    if not (token and chat):
        print(f"Telegram: Token gesetzt: {bool(token)}, Chat-ID gesetzt: {bool(chat)} -> nichts gesendet")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text},
            timeout=20,
        )
        print(f"Telegram: Status {r.status_code} {r.text[:200]}")
    except requests.RequestException as e:
        print(f"Telegram Netzwerkfehler: {e}")

URL = "https://www.stw-thueringen.de/mensen/schmalkalden/mensa-blechhammer.html"
NAME = "Mensa Blechhammer"
TZ = ZoneInfo("Europe/Berlin")
WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

# Jeder Gerichtseintrag hat einen "Gericht bewerten"-Link mit Name und Datum in der Adresse.
LINK_RE = re.compile(r"essen=(.*?)&datum=(\d{2}\.\d{2}\.\d{4})")
PRICE_RE = re.compile(r"(\d+,\d{2})\s*/\s*(\d+,\d{2})\s*/\s*(\d+,\d{2})\s*€")
LINK_SELECTOR = 'a[href*="lob-und-kritik"]'


def parse_menu(html):
    """Gibt eine Liste von Gerichten zurück: title, date, price (Studierende), icon-Kennzeichen."""
    soup = BeautifulSoup(html, "html.parser")
    dishes, seen = [], set()
    for a in soup.select(LINK_SELECTOR):
        m = LINK_RE.search(a.get("href", ""))
        if not m:
            continue
        title, date = unquote(m.group(1)).strip(), m.group(2)
        if (title, date) in seen:
            continue
        seen.add((title, date))

        # Kleinsten umgebenden Block suchen, der diesen Link UND einen Preis enthält,
        # aber kein zweites Gericht (sonst würden sich Preise/Symbole vermischen).
        price, block = None, a
        while block.parent is not None and block.parent.name not in ("[document]", "html", "body"):
            block = block.parent
            if len(block.select(LINK_SELECTOR)) > 1:
                block = None
                break
            pm = PRICE_RE.search(block.get_text(" ", strip=True))
            if pm:
                price = pm.group(1)
                break
        else:
            block = None
        if price is None:
            block = None

        labels = ""
        if block is not None:
            labels = " | ".join(
                f"{i.get('alt') or ''} {i.get('title') or ''}" for i in block.find_all("img")
            )
        dishes.append(
            {
                "title": title,
                "date": date,
                "price": price,
                "vegan": "Vegane" in labels,
                "veg": "Vegetarische" in labels,
                "meat": any(w in labels for w in ("Rindfleisch", "Schweinefleisch", "Geflügel", "Lamm", "Wild")),
                "fish": "Fisch" in labels,
            }
        )
    return dishes


def icon(d):
    if d["vegan"]:
        return "🌱"
    if d["veg"]:
        return "🥕"
    if d["fish"]:
        return "🐟"
    if d["meat"]:
        return "🍖"
    return "🍽"


def format_message(dishes, watch=()):
    date = dishes[0]["date"]
    dt = datetime.strptime(date, "%d.%m.%Y")
    head = f"🍽 {NAME}, {WEEKDAYS[dt.weekday()]} {dt.strftime('%d.%m.')}"
    if dt.date() != datetime.now(TZ).date():
        head += " (nicht heute!)"
    lines, hits = [head], 0
    for d in dishes:
        star = ""
        if any(w and w in d["title"].lower() for w in watch):
            star, hits = "⭐ ", hits + 1
        price = f" ({d['price']} €)" if d["price"] else ""
        lines.append(f"{icon(d)} {star}{d['title']}{price}")
    if hits:
        lines.insert(1, "⭐ Eines deiner Stichwörter steht auf dem Plan!")
    return "\n".join(lines)


def main():
    dry = "--dry" in sys.argv
    send = print if dry else notify
    watch = [w.strip().lower() for w in (os.getenv("MENSA_WATCH") or "").split(",") if w.strip()]

    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9"})
    try:
        r = get_retry(s, URL, timeout=20)
    except requests.RequestException as e:
        send(f"⚠️ Mensaplan: Netzwerkfehler ({type(e).__name__})")
        sys.exit(1)
    if r.status_code != 200:
        send(f"⚠️ Mensaplan: Seite antwortet mit Fehler {r.status_code}")
        sys.exit(1)

    dishes = parse_menu(r.content)
    if not dishes:
        if "keine Essen angeboten" in r.text:
            print("Mensa hat heute keine Essen im Angebot, keine Nachricht.")
            return
        send("⚠️ Mensaplan: keine Gerichte gefunden, evtl. hat sich die Seite geändert.")
        sys.exit(1)
    send(format_message(dishes, watch))


if __name__ == "__main__":
    main()
