#!/usr/bin/env python3
"""
systembolaget_vivino.py

Hämtar viner från Systembolagets sortiment och korskör dem mot Vivino
för att hitta betyg (rating) på respektive vin.

TVÅ LÄGEN FÖR SORTIMENTSDATA
----------------------------
1) CSV-läge (rekommenderas, mest tillförlitligt):
   Ladda ner "Sortiment i Excelformat" från systembolaget.se, spara/exportera
   som CSV, och kör:

       python systembolaget_vivino.py --source csv --file sortiment.csv

   Scriptet försöker själv känna igen kolumnerna för namn, producent och
   varugrupp oavsett exakt kolumnnamn (Systembolaget ändrar dem ibland).

2) Butiks-läge (EXPERIMENTELLT):
   Använder Systembolagets interna sök-API (samma som webbplatsen använder
   internt). Det är INTE officiellt, kräver ingen inloggning men kan sluta
   fungera om Systembolaget ändrar sin frontend. Om det slutar fungera:
   öppna systembolaget.se, sök på ett vin, öppna webbläsarens nätverksflik
   och leta efter anropet till api-extern.systembolaget.se/.../productsearch
   för att se om URL/headers/parametrar ändrats.

       python systembolaget_vivino.py --source store --store-id 0102

   Butiks-ID hittar du genom att söka på din butik på systembolaget.se —
   det syns i URL:en till butikssidan.

VIVINO-MATCHNING
-----------------
Vivino saknar ett officiellt publikt API. Scriptet använder deras interna
sökendpoint (samma som används av flera open-source-projekt, t.ex.
Home Assistant-integrationer). Matchning görs med fuzzy string matching
mellan Systembolagets produktnamn+producent och Vivino-träffar. Osäkra
matchningar (låg similarity-score) flaggas i output så du kan dubbelkolla
manuellt istället för att lita blint på dem.

INSTALLATION
------------
    pip install requests pandas rapidfuzz

OUTPUT
------
En CSV-fil, wines_with_ratings.csv, med Systembolagets produktinfo plus
Vivino-betyg, antal recensioner, och matchningssäkerhet — sorterad efter
högst betyg först.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

import unicodedata


def _normalize_text(s: str) -> str:
    # Gemener (rapidfuzz normaliserar inte reliabelt versaler på accenttecken,
    # t.ex. gav "FRÈRES" vs "Frères" 0.19 istället för 1.0) OCH ta bort
    # diakritiska tecken (annars särskiljs t.ex. "Áster" ifrån "Aster" och
    # "Tikveš" ifrån "Tikves" som helt olika ord trots att de är samma namn
    # skrivet med/utan accent i Systembolagets respektive Vivinos data).
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


try:
    from rapidfuzz import fuzz
    def similarity(a: str, b: str) -> float:
        return fuzz.token_sort_ratio(_normalize_text(a), _normalize_text(b)) / 100.0
    def producer_similarity(a: str, b: str) -> float:
        # token_set_ratio ignorerar ord som bara finns i den ena strängen, vilket
        # passar producentnamn bra: "Viñedos Emiliana" mot Vivinos winery-namn
        # "Emiliana" ska räknas som samma producent trots det extra ordet.
        return fuzz.token_set_ratio(_normalize_text(a), _normalize_text(b)) / 100.0
except ImportError:
    # Fallback om rapidfuzz inte är installerat: enklare inbyggd matchning
    from difflib import SequenceMatcher
    def similarity(a: str, b: str) -> float:
        return SequenceMatcher(None, _normalize_text(a), _normalize_text(b)).ratio()
    producer_similarity = similarity

import pandas as pd


SYSTEMBOLAGET_SEARCH_URL = "https://api-extern.systembolaget.se/sb-api-ecommerce/v2/productsearch/search"
# OBS: Nyckeln nedan hittades 2026-08-05 hårdkodad i Systembolagets egen
# Next.js-frontend-bundle (NEXT_PUBLIC_API_KEY_APIM), dvs den är avsiktligt
# publik och skickas av alla besökares webbläsare. Den kan ändå rotera vid
# en frontend-deploy. Om butiksläget slutar fungera: öppna systembolaget.se,
# sök på ett vin, öppna nätverksfliken och leta efter anropet till
# productsearch/search för att hämta ett färskt värde.
SYSTEMBOLAGET_API_KEY_PLACEHOLDER = "8d39a7340ee7439f8b4c1e995c8f3e4a"

VIVINO_SEARCH_URL = "https://www.vivino.com/api/explore/explore"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"
# OBS: Google har stängt Custom Search JSON API för nya kunder (bekräftat av
# Google support 2026-04-07) — GOOGLE_CSE_URL-fallbacken ovan fungerar bara för
# konton som redan hade API:et aktiverat innan stängningen. Vertex AI Search
# (Discovery Engine) nedan är den väg Google pekar nya kunder mot istället.

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; wine-cross-reference-script/1.0)"
}


@dataclass
class Wine:
    varunummer: str
    namn: str
    producent: str
    pris: Optional[str] = None
    volym: Optional[str] = None
    ursprung: Optional[str] = None
    grapes: Optional[str] = None
    vivino_rating: Optional[float] = None
    vivino_reviews: Optional[int] = None
    match_confidence: Optional[float] = None
    vivino_url: Optional[str] = None
    match_source: Optional[str] = None


# ---------------------------------------------------------------------------
# Läge 1: CSV-import av (hela eller egen) sortimentsfil
# ---------------------------------------------------------------------------

def load_from_csv(path: str) -> list:
    df = pd.read_csv(path, sep=None, engine="python", encoding="utf-8-sig")
    df.columns = [c.strip() for c in df.columns]

    def find_col(candidates):
        for c in df.columns:
            lc = c.lower()
            if any(cand in lc for cand in candidates):
                return c
        return None

    col_namn = find_col(["namn", "produktnamn", "name"])
    col_producent = find_col(["producent", "leverant", "producer"])
    col_varugrupp = find_col(["varugrupp", "categori", "category"])
    col_varunr = find_col(["varunummer", "artikelnummer", "id"])
    col_pris = find_col(["pris"])
    col_volym = find_col(["volym"])
    col_ursprung = find_col(["ursprung", "land", "country"])

    if col_namn is None:
        print("Kunde inte hitta en namn-kolumn i CSV:n. Kolumner som hittades:", list(df.columns))
        sys.exit(1)

    wines = []
    for _, row in df.iterrows():
        varugrupp = str(row.get(col_varugrupp, "")) if col_varugrupp else ""
        # Filtrera till vin om vi kan avgöra varugrupp, annars ta med allt
        if col_varugrupp and "vin" not in varugrupp.lower() and "sake" not in varugrupp.lower():
            continue
        wines.append(Wine(
            varunummer=str(row.get(col_varunr, "")) if col_varunr else "",
            namn=str(row.get(col_namn, "")).strip(),
            producent=str(row.get(col_producent, "")).strip() if col_producent else "",
            pris=str(row.get(col_pris, "")) if col_pris else None,
            volym=str(row.get(col_volym, "")) if col_volym else None,
            ursprung=str(row.get(col_ursprung, "")) if col_ursprung else None,
        ))
    return wines


# ---------------------------------------------------------------------------
# Läge 2: Butiksspecifikt sortiment via (oofficiellt) sök-API
# ---------------------------------------------------------------------------

# Systembolagets categoryLevel1="Vin" innehåller mer än dryck gjord på druvor:
# Glögg, Vermouth, Sake, Aperitifer och Starkvin ligger också i "Vin"-kategorin.
# Standard är att bara ta med de fyra "riktiga" vinkategorierna.
WINE_CATEGORIES = {"Rött vin", "Vitt vin", "Mousserande vin", "Rosévin"}


def load_from_store(store_id: str, api_key: str, include_assortment: Optional[set] = None,
                     include_category2: Optional[set] = WINE_CATEGORIES) -> list:
    wines = []
    page = 0
    page_size = 30
    headers = dict(REQUEST_HEADERS)
    headers["Ocp-Apim-Subscription-Key"] = api_key

    while True:
        params = {
            "categoryLevel1": "Vin",
            "storeId": store_id,
            "isInStoreAssortmentSearch": "true",
            "page": page,
            "size": page_size,
        }
        resp = requests.get(SYSTEMBOLAGET_SEARCH_URL, headers=headers, params=params, timeout=20)
        if resp.status_code == 404 and page > 0:
            # Systembolagets API svarar 404 (istället för en tom lista) när man
            # begär en sida bortom sortimentets slut — normalt slut på pagineringen.
            break
        if resp.status_code != 200:
            print(f"Fel vid hämtning från Systembolaget (status {resp.status_code}). "
                  f"API-nyckeln/endpointen kan behöva uppdateras — se kommentar i toppen av filen.")
            break
        data = resp.json()
        products = data.get("products", [])
        if not products:
            break
        for p in products:
            if include_assortment and p.get("assortmentText") not in include_assortment:
                continue
            if include_category2 and p.get("categoryLevel2") not in include_category2:
                continue
            wines.append(Wine(
                varunummer=str(p.get("productNumber", "")),
                namn=((p.get("productNameBold") or "") + " " + (p.get("productNameThin") or "")).strip(),
                producent=p.get("producerName") or "",
                pris=str(p.get("price", "")),
                volym=p.get("volumeText", "") or str(p.get("volume", "")),
                ursprung=p.get("country", "") or "",
                grapes=", ".join(p.get("grapes") or []) or None,
            ))
        page += 1
        time.sleep(0.5)  # var snäll mot API:et
        if page > 50:  # säkerhetsspärr
            break

    # Sortimentet kan förskjutas mellan sidhämtningar (produkter läggs till/tas bort
    # medan vi paginerar), vilket ibland ger samma vin på två sidor. Deduplicera på
    # varunummer och behåll ordningen.
    seen = set()
    deduped = []
    for w in wines:
        if w.varunummer in seen:
            continue
        seen.add(w.varunummer)
        deduped.append(w)
    return deduped


# ---------------------------------------------------------------------------
# Vivino-matchning
# ---------------------------------------------------------------------------

def search_vivino(query: str) -> list:
    # Vivinos explore-API kräver att minst ett "filter" utöver search_term sätts,
    # annars svarar det 400 "at least one filter should be set". Värdena nedan
    # (country_code/currency_code/grape_filter/order/order_by/price_range/min_rating)
    # är samma default-filter som vivino.com självt skickar vid en vanlig sökning.
    params = {
        "search_term": query,
        "per_page": 20,
        "country_code": "se",
        "currency_code": "SEK",
        "grape_filter": "varietal",
        "order": "desc",
        "order_by": "relevance",
        "price_range_min": 0,
        "price_range_max": 100000,
        "min_rating": 0,
    }
    try:
        resp = requests.get(VIVINO_SEARCH_URL, headers=REQUEST_HEADERS, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        matches = data.get("explore_vintage", {}).get("matches", [])
        results = []
        for m in matches:
            vintage = m.get("vintage", {})
            wine = vintage.get("wine", {})
            wine_id = wine.get("id")
            winery_name = wine.get("winery", {}).get("name", "")
            wine_name = wine.get("name", "")
            results.append({
                "name": f"{winery_name} {wine_name}".strip(),
                "winery": winery_name,
                "wine_name": wine_name,
                "rating": vintage.get("statistics", {}).get("ratings_average"),
                "reviews": vintage.get("statistics", {}).get("ratings_count"),
                # OBS: "/wines/{id}" är INTE en giltig Vivino-URL — den dirigerar om till
                # ett helt annat, orelaterat vin. Rätt mönster (verifierat manuellt mot
                # vivino.com) är "/w/{wine_id}", som Vivino i sin tur skickar vidare till
                # vinets fullständiga URL med rätt namn/producent i sluggen.
                "url": f"https://www.vivino.com/w/{wine_id}" if wine_id else None,
            })
        return results
    except (requests.RequestException, ValueError):
        return []


_VIVINO_WINE_ID_RE = re.compile(r"vivino\.com/(?:[a-z]{2}/)?(?:[^/?]+/)?w/(\d+)")
_VIVINO_STATS_RE = re.compile(
    r'"statistics":\{"status":"(?:Normal|BelowThreshold)","ratings_count":(\d+),"ratings_average":([\d.]+)'
)
_VIVINO_COUNTRY_RE = re.compile(r'"country":\{"code":"[a-z]{2}","name":"([^"]*)"')
_VIVINO_GRAPES_BLOCK_RE = re.compile(r'"grapes":\[(.*?)\]')
_VIVINO_WINERY_RE = re.compile(r'"winery":\{"id":\d+,"name":"([^"]*)"')
_VIVINO_WINE_NAME_RE = re.compile(r'"wine":\{"id":\d+,"name":"([^"]*)"')
_VIVINO_GRAPE_NAME_RE = re.compile(r'"name":"([^"]*)"')
_VIVINO_PRELOADED_STATE_RE = re.compile(r"window\.__PRELOADED_STATE__\.winePageInformation = (\{.*?\});\s*\n")


def parse_sb_volume_ml(volym: Optional[str]) -> Optional[int]:
    """Tolkar Systembolagets Volym-fält till total mängd ml — inklusive
    flerflaskeförpackningar ("3 flaskor à 750ml" -> 2250), så att priset kan
    normaliseras till kr/750ml oavsett flaskstorlek innan det jämförs mot
    Vivinos marknadspris (se get_vivino_market_price_per_750ml)."""
    if not volym:
        return None
    v = volym.strip()
    m = re.match(r"^(\d+)\s*ml$", v)
    if m:
        return int(m.group(1))
    m = re.match(r"^(\d+)\s*flaskor\s*à\s*(\d+)\s*ml$", v)
    if m:
        return int(m.group(1)) * int(m.group(2))
    return None


def get_vivino_market_price_per_750ml(wine_id: str) -> Optional[dict]:
    """Hämtar Vivinos marknadspris (median av aktiva återförsäljarpriser) för
    vinets primära/aktuella årgång, normaliserat till kr/750ml. En årgång kan
    ha återförsäljare med olika flaskstorlekar (t.ex. både 750ml och magnum)
    i samma prislista — normalisera VARJE enskilt pris till 750ml-ekvivalent
    innan medianen räknas ut, annars blandas storlekarna ihop till ett
    missvisande snittpris.

    Returnerar None om vinet saknar aktiva återförsäljare på Vivino (vanligt
    för vardagsviner — Vivinos marknadsprisdata täcker mest viner med en
    aktiv andrahands-/samlarmarknad, inte hela sortimentet)."""
    try:
        resp = requests.get(f"https://www.vivino.com/w/{wine_id}", headers=REQUEST_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        m = _VIVINO_PRELOADED_STATE_RE.search(resp.text)
        if not m:
            return None
        data = json.loads(m.group(1))
    except (requests.RequestException, json.JSONDecodeError):
        return None

    vintages = ((data.get("checkout_prices") or {}).get("vintages")) or []
    if not vintages:
        return None

    # Samma årgång som toppfältet "price" pekar på — den Vivino visar som
    # huvudpris på sidan. Faller tillbaka på första årgången i listan om
    # toppfältet saknas.
    default_vintage_id = (data.get("price") or {}).get("vintage_id")
    chosen = next(
        (v for v in vintages if v["availability"]["vintage"]["id"] == default_vintage_id),
        vintages[0],
    )

    normalized = []
    for merchant in chosen.get("merchants_with_prices", []):
        for p in merchant.get("prices", []):
            amount = p.get("amount")
            vol_ml = (p.get("bottle_type") or {}).get("volume_ml")
            if amount is None or not vol_ml or (p.get("currency") or {}).get("code") != "SEK":
                continue
            normalized.append(amount / (vol_ml / 750))

    if not normalized:
        return None
    normalized.sort()
    n = len(normalized)
    median = normalized[n // 2] if n % 2 else (normalized[n // 2 - 1] + normalized[n // 2]) / 2
    return {
        "pris_per_750ml": round(median, 2),
        "prispunkter": n,
        "argang": chosen["availability"]["vintage"]["year"],
    }


def _json_string_unescape(raw: str) -> str:
    """Regexarna ovan plockar ut den råa insidan av en JSON-sträng direkt ur
    HTML-sidans embedded JSON, utan att avkoda JSON-escapes. Vivino escapar
    t.ex. "&" som "\\u0026", vilket annars läcker igenom som bokstavlig text
    "\\u0026" — upptäckt när "Palmer & Co" jämfördes mot den oavkodade
    "Palmer \\u0026 Co." och floppade en ordmatchning som borde klarat sig."""
    try:
        return json.loads(f'"{raw}"')
    except (json.JSONDecodeError, ValueError):
        return raw


def search_google_for_vivino_wine(query: str, api_key: str, cx: str, num: int = 3) -> list:
    """Söker via Googles Custom Search JSON API (sökmotorn är avgränsad till
    vivino.com) och returnerar Vivino-vin-ID:n i rankad ordning. Används bara
    som fallback när Vivinos egen sökning inte gav en godkänd träff — Vivinos
    interna sökrankning viktar tydligt mot antal recensioner, så nya/småskaliga
    viner kan saknas helt i dess resultat trots att de finns på Vivino."""
    params = {"key": api_key, "cx": cx, "q": query, "num": num}
    try:
        resp = requests.get(GOOGLE_CSE_URL, params=params, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        wine_ids = []
        for item in data.get("items", []):
            m = _VIVINO_WINE_ID_RE.search(item.get("link", ""))
            if m and m.group(1) not in wine_ids:
                wine_ids.append(m.group(1))
        return wine_ids
    except (requests.RequestException, ValueError):
        return []


_vertex_token_cache = {"token": None, "fetched_at": 0.0}


def get_gcloud_access_token() -> Optional[str]:
    """Hämtar en access token via lokalt inloggad gcloud CLI (`gcloud auth
    login` måste ha körts). Cachas i ~45 minuter — gcloud-tokens är giltiga i
    ~60 minuter och en full körning kan ta ett tag."""
    import shutil
    import subprocess
    now = time.time()
    if _vertex_token_cache["token"] and (now - _vertex_token_cache["fetched_at"]) < 45 * 60:
        return _vertex_token_cache["token"]
    gcloud_path = shutil.which("gcloud")
    if not gcloud_path:
        return None
    try:
        # subprocess kan inte hitta "gcloud" direkt på Windows eftersom det är en
        # .CMD-wrapper (kräver PATHEXT-upplösning som bara sker i ett skal) —
        # shutil.which hittar rätt fil (t.ex. gcloud.CMD) åt oss istället.
        result = subprocess.run([gcloud_path, "auth", "print-access-token"],
                                 capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            return None
        token = result.stdout.strip()
        _vertex_token_cache["token"] = token
        _vertex_token_cache["fetched_at"] = now
        return token
    except (OSError, subprocess.SubprocessError):
        return None


def search_vertex_ai_for_vivino_wine(query: str, project_number: str, engine_id: str, page_size: int = 10) -> list:
    """Söker via Vertex AI Search (Discovery Engine) mot en 'basic website
    search'-datastore avgränsad till vivino.com. Används som fallback när
    Vivinos egen sökning inte gav en godkänd träff — se GOOGLE_CSE_URL-
    kommentaren ovan för varför detta ersatte Custom Search JSON API."""
    token = get_gcloud_access_token()
    if not token:
        return []
    url = (f"https://discoveryengine.googleapis.com/v1alpha/projects/{project_number}/locations/global/"
           f"collections/default_collection/engines/{engine_id}/servingConfigs/default_search:search")
    body = {
        "query": query,
        "pageSize": page_size,
        "queryExpansionSpec": {"condition": "AUTO"},
        "spellCorrectionSpec": {"mode": "AUTO"},
        "languageCode": "en-US",
    }
    try:
        resp = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                              json=body, timeout=15)
        if resp.status_code != 200:
            return []
        data = resp.json()
        wine_ids = []
        for r in data.get("results", []):
            link = r.get("document", {}).get("derivedStructData", {}).get("link", "")
            m = _VIVINO_WINE_ID_RE.search(link)
            if m and m.group(1) not in wine_ids:
                wine_ids.append(m.group(1))
        return wine_ids
    except (requests.RequestException, ValueError):
        return []


def get_vivino_wine_by_id(wine_id: str) -> Optional[dict]:
    """Hämtar betyg (+ land och druvor, för korsvalidering mot Systembolagets
    egna fält) för ett specifikt Vivino-vin-ID. OBS: "/wines/{id}" är fel
    URL-mönster (se search_vivino) — rätt är "/w/{id}"."""
    try:
        resp = requests.get(f"https://www.vivino.com/w/{wine_id}", headers=REQUEST_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        m = _VIVINO_STATS_RE.search(resp.text)
        if not m:
            return None
        country_m = _VIVINO_COUNTRY_RE.search(resp.text)
        grapes_m = _VIVINO_GRAPES_BLOCK_RE.search(resp.text)
        grapes = [_json_string_unescape(g) for g in _VIVINO_GRAPE_NAME_RE.findall(grapes_m.group(1))] if grapes_m else []
        winery_m = _VIVINO_WINERY_RE.search(resp.text)
        wine_name_m = _VIVINO_WINE_NAME_RE.search(resp.text)
        return {
            "rating": float(m.group(2)),
            "reviews": int(m.group(1)),
            "url": resp.url,
            "country": _json_string_unescape(country_m.group(1)) if country_m else None,
            "grapes": grapes,
            "winery": _json_string_unescape(winery_m.group(1)) if winery_m else None,
            "wine_name": _json_string_unescape(wine_name_m.group(1)) if wine_name_m else None,
        }
    except requests.RequestException:
        return None


def _build_query_variants(wine: Wine) -> list:
    """Bygger sökfrågor att pröva i tur och ordning tills en godkänd träff hittas.
    Vivinos sökning fungerar ungefär som ett AND mellan sökorden, så Systembolagets
    fullständiga juridiska producentnamn (t.ex. "X Vigne e Vini", "Y Wines Pty Ltd")
    ger ofta 0 träffar även när vinet finns på Vivino under ett enklare namn."""
    variants = []
    # Systembolagets produktnamn innehåller ibland redan producentnamnet
    # (t.ex. "Xavier Vignon Châteauneuf-du-Pape" för producenten "Xavier
    # Vignon") — undvik att dubblera det i frågan ("Xavier Vignon Xavier
    # Vignon Châteauneuf-du-Pape").
    if wine.producent and wine.namn and _normalize_text(wine.namn).startswith(_normalize_text(wine.producent)):
        full = wine.namn.strip()
    else:
        full = f"{wine.producent} {wine.namn}".strip()
    if full:
        variants.append(full)
    if wine.namn and wine.namn != full:
        variants.append(wine.namn)
    if wine.producent and wine.grapes:
        variants.append(f"{wine.producent} {wine.grapes}")
    return variants


# Manuell koll av testviner mot de faktiska Vivino-sidorna visade att en enda
# blandad similarity-score på "producent+namn" gav falska positiva: samma
# producent men fel serie ("Family Selection Brut Cuvée" vs "...Shiraz
# Cabernet"), eller helt fel producent fast med gemensamma ord som druva/region.
# Genom att kräva rimlig likhet på producent OCH produktnamn separat (istället
# för en snittad sträng) fångar vi upp legitima stavnings-/ordföljdsskillnader
# utan att öppna för de tydligaste fel-producent-träffarna.
PRODUCER_SIM_THRESHOLD = 0.55
NAME_SIM_THRESHOLD = 0.6

# Generiska ord i producentnamn (flera språk) som inte säger något om VILKEN
# producent det är. token_set_ratio kan ge ett högt score bara för att två
# producenter delar strukturord som "Domaine de/des" — upptäckt när "Domaine
# des Lauriers" (fel) matchade "Domaine de la Rouletière" (rätt vin, fel
# producent) med sim=0.77, långt över tröskeln.
_PRODUCER_STOPWORDS = {
    "vin", "vins", "vini", "vinos", "vinho", "vinhos", "wine", "wines", "winery",
    "wineries", "vignobles", "vignoble", "vigne", "domaine", "domaines", "chateau",
    "château", "cellars", "cellar", "estate", "estates", "family", "selection",
    "pty", "ltd", "llc", "inc", "ab", "sa", "srl", "spa", "gmbh", "bodega",
    "bodegas", "cantina", "cantine", "weingut", "and", "de", "des", "du", "e",
    "la", "le", "les", "el", "vina", "fils", "et", "champagne", "familia",
    "grand", "grande",
}


# Generiska ord i PRODUKTnamn (kvalitetstermer utan formell betydelse, inte
# druvor/regioner) som inte säger något om VILKET vin det är.
#
# OBS: "Reserva"/"Crianza"/"Gran Reserva"/"Riserva"/"Selezione" etc. är INTE
# med här längre trots att de en gång stoppordades (efter "Lat 42 Reserva",
# 129 kr vardagsvin, som matchade "Gran Reserva 890", La Rioja Altas
# flaggskeppsvin, på det enda gemensamma ordet "Reserva"). De är formella,
# reglerade lagringskategorier (särskilt spanska/italienska), inte
# marknadsföringsfluff — och tas nu bort AV MISSTAG när samma producent har
# flera olika kuvéer på olika nivåer: "Marqués de Riscal Reserva" matchade
# "Tapias de Marqués de Riscal" (samma producent, annan specifik kuvé) enbart
# för att "Reserva" filtrerades bort och inget annat särskiljande ord fanns
# kvar i SB:s produktnamn. Numera är producer_match() (byggd senare samma
# session) den huvudsakliga spärren mot fel-producent-kollisioner som
# "Reserva" ursprungligen skulle skydda mot, så ordet behövs inte längre där.
_PRODUCT_NAME_STOPWORDS = {
    # "Classico" utelämnas medvetet (t.ex. Chianti Classico är en egen,
    # reglerad delzon, inte bara "klassisk stil") — samma logik som Reserva.
    "especial", "special", "classic", "premium", "superiore", "superior",
    "vin", "vinho", "wine", "organic", "eco", "ekologisk",
    # Färg-/stilord som beskriver kategori, inte den specifika produkten.
    # Upptäckt när "Dom Pérignon Blanc" (SB) mot Vivinos "Brut Champagne"
    # (samma vin, korrekt matchning) flaggades felaktigt eftersom "Blanc" och
    # "Brut" inte delades av båda sidor trots att det är rätt vin.
    "blanc", "blancs", "rouge", "rosé", "rose", "brut", "sec", "extra",
    "dry", "sweet", "vitt", "rött", "torr", "white", "red",
    # Producent-strukturord (redan stoppord i _PRODUCER_STOPWORDS) som
    # Systembolaget ofta vävt in i själva PRODUKTnamnet — Vivinos eget
    # wine_name upprepar dem sällan eftersom winery-fältet redan har dem.
    # Upptäckt när "Château Tirecul La Gravière Les Pins" (SB) föll mot
    # Vivinos "Tirecul la Gravière" + "Les Pins Monbazillac" enbart för att
    # "Château" inte fanns i winery-fältet, och likadant "Domaine Roux" i
    # "Bourgogne Les Murelles Domaine Roux" mot winery "Roux Père & Fils".
    "chateau", "château", "domaine", "domaines",
}


def _distinctive_words(name: str, stopwords: set = _PRODUCER_STOPWORDS) -> set:
    # "-" OCH apostrof-varianter (’ ‘ ʼ ´ ` samt raka ') splittas till separata
    # ord, precis som "-": annars blir "Langlois-Chateau" ett enda token som
    # aldrig delas med bara "Langlois" — och likadant fuserar en borttagen
    # (snarare än mellanslagen) apostrof ihop "dell'Ornellaia" till
    # "dellornellaia", som aldrig delas med Vivinos separata "Ornellaia".
    # Måste också hantera att Systembolaget skriver typografisk apostrof
    # (’, U+2019) där Vivino skriver rak (') — t.ex. "d’Abruzzo" vs
    # "d'Abruzzo" — annars blir de två olika ord trots att de är samma.
    cleaned = _normalize_text(name).replace(".", "").replace(",", "").replace("-", " ")
    for apostrophe_char in "'’‘ʼ´`":
        cleaned = cleaned.replace(apostrophe_char, " ")
    words = cleaned.split()
    return {w for w in words if len(w) > 3 and w not in stopwords}


def shares_distinctive_word(a: str, b: str, stopwords: set = _PRODUCER_STOPWORDS) -> bool:
    return bool(_distinctive_words(a, stopwords) & _distinctive_words(b, stopwords))


def distinctive_words_subset_match(a: str, b: str, stopwords: set = _PRODUCER_STOPWORDS) -> Optional[bool]:
    """Strängare än shares_distinctive_word: kräver att ALLA utmärkande ord på
    den kortare sidan finns på den andra sidan, inte bara att NÅGOT ord delas.

    Upptäckt via tre bekräftade felträffar som alla klarade det gamla "delar
    minst ett ord"-kravet: "Pierre Olivier" mot "Pierre Morlet et Fils" (delade
    bara det generiska förnamnet "Pierre", inte efternamnet), "Crozes-Hermitage
    Les Launes" mot "Les Bessards Hermitage" (delade bara appellationsordet
    "Hermitage", inte vingården), och "Viña Maipo" mot "Viña Cobos" (delade
    bara det spanska "vingård"-ordet — numera egen stoppord ovan). Ett enda
    gemensamt ord räcker inte om motparten har fler ord som INTE matchar."""
    wa, wb = _distinctive_words(a, stopwords), _distinctive_words(b, stopwords)
    if not wa or not wb:
        return None
    shorter, longer = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return shorter.issubset(longer)


def producer_match(sb_producer: str, sb_namn: str, vivino_winery: str) -> Optional[bool]:
    """Producent-varianten av distinctive_words_subset_match, men prövar båda
    riktningarna eftersom en enkel "kortaste sidan"-jämförelse gav falska
    negativ när EN sida bara har EXTRA kvalificerande ord, inte MOTSTRIDIGA:

    - Vivino lägger ibland till förnamn/prefix som Systembolaget saknar helt
      ("Schwarz" → Vivinos "Johann Schwarz", "Beaumont des Crayères" →
      Vivinos "Champagne Beaumont des Crayeres") — här räcker det att SB:s
      (rena) producentnamn är en delmängd av Vivinos.
    - Omvänt bär SB:s eget produktnamn ibland undermärket som Vivino listar
      som egen winery, medan SB:s Producent-fält är moderbolaget ("Moët &
      Chandon" → Vivinos "Dom Pérignon") — här krävs Producent+Namn ihopslaget
      för att Vivinos (korta) winery-namn ska bli en delmängd.

    Godkänns om ENTINGEN riktning håller. Äkta fel-producenter (t.ex. "Pierre
    Olivier" mot "Pierre Morlet", eller "Miguel Torres" mot "Familia Torres"
    innan "familia" blev stoppord) klarar ingen av riktningarna, eftersom
    båda sidor då har egna ord som saknas hos motparten."""
    vivino_words = _distinctive_words(vivino_winery, _PRODUCER_STOPWORDS)
    sb_producer_words = _distinctive_words(sb_producer, _PRODUCER_STOPWORDS)
    sb_combined_words = _distinctive_words(f"{sb_producer} {sb_namn}", _PRODUCER_STOPWORDS)
    if not vivino_words or not (sb_producer_words or sb_combined_words):
        return None
    vivino_subset_of_combined = bool(sb_combined_words) and vivino_words.issubset(sb_combined_words)
    producer_subset_of_vivino = bool(sb_producer_words) and sb_producer_words.issubset(vivino_words)
    return vivino_subset_of_combined or producer_subset_of_vivino


# Systembolaget anger land på svenska, Vivino på engelska — det är olika
# SPRÅK, inte stavvarianter, så fuzzy strängmatchning (rapidfuzz) missar de
# flesta även med generöst tröskelvärde. Upptäckt när "Nya Zeeland" mot
# Vivinos "New Zealand" bara gav sim=0.73 (under 0.8-tröskeln) och därmed
# felaktigt underkände en annars korrekt matchning (Rua Pinot Noir/Akarua).
# Ett stickprov av alla 15 svenska landnamn i sortimentet visade att 10 av 15
# föll under tröskeln på samma sätt — detta är alltså inget enstaka undantag
# utan en systematisk brist som sannolikt påverkat matchningar hela
# sessionen (och innan den).
_SWEDISH_TO_ENGLISH_COUNTRY = {
    # Nycklarna är redan NFKD-normaliserade (diakritiska tecken borttagna,
    # t.ex. "österrike" -> "osterrike") eftersom uppslaget görs mot
    # _normalize_text(sb_ursprung) — en rå "ö" i nyckeln skulle aldrig matcha.
    "frankrike": "france", "italien": "italy", "spanien": "spain",
    "tyskland": "germany", "osterrike": "austria", "sydafrika": "south africa",
    "nya zeeland": "new zealand", "australien": "australia", "ungern": "hungary",
    "grekland": "greece", "bulgarien": "bulgaria", "libanon": "lebanon",
    "serbien": "serbia", "luxemburg": "luxembourg",
    "nordmakedonien": "north macedonia", "georgien": "georgia",
    "ukraina": "ukraine", "rumanien": "romania", "tjeckien": "czechia",
}


def country_match(sb_ursprung: Optional[str], vivino_country: Optional[str]) -> Optional[bool]:
    """Jämför Systembolagets (svenska) och Vivinos landnamn. Vivino svarar
    INTE konsekvent på engelska — samma get_vivino_wine_by_id-anrop kan ge
    "Austria" för ett vin och "Österrike" för ett annat (troligen beroende på
    vilken språkvariant av sidan som råkade svara), upptäckt när den första
    versionen av denna funktion (bara översatt->engelska) plötsligt
    underkände en hel drös redan korrekta träffar (Leth, Ecologica, Raccolto)
    där Vivino råkade svara på svenska. Provar därför båda hållen: rakt
    (samma språk på båda sidor) OCH via översättning (olika språk)."""
    if not sb_ursprung or not vivino_country:
        return None
    if producer_similarity(sb_ursprung, vivino_country) > 0.8:
        return True
    translated = _SWEDISH_TO_ENGLISH_COUNTRY.get(_normalize_text(sb_ursprung).strip())
    if translated and producer_similarity(translated, vivino_country) > 0.8:
        return True
    return False


def _dominant_grape_present(sb_druvor: Optional[str], vivino_grapes: list) -> bool:
    """Kollar om NÅGON av Systembolagets angivna druvor finns hos Vivino, inte
    bara den FÖRST listade. Den gamla "bara första druvan"-varianten missade
    "Petite Cuvée Madame" (SB anger "Syrah, Cinsault, Grenache, Mourvèdre,
    Cabernet Sauvignon" — en bred sortimentslista, inte nödvändigtvis i den
    specifika kuvéns verkliga ordning — där Vivino bara anger "Grenache,
    Cinsault" för just den kuvén: två av SB:s fem druvor stämde, men inte den
    första, så druv-kollen gav False trots verklig överlappning)."""
    if not sb_druvor or not vivino_grapes:
        return False
    sb_grapes = [g.strip() for g in sb_druvor.split(",") if g.strip()]
    if not sb_grapes:
        return False
    return any(producer_similarity(sb_g, viv_g) > 0.7 for sb_g in sb_grapes for viv_g in vivino_grapes)


def candidate_accepted(producer_ok: Optional[bool], product_ok: Optional[bool],
                        country_ok: Optional[bool] = None, grape_ok: Optional[bool] = None) -> bool:
    """Kombinerar producer_match + distinctive_words_subset_match (+ land/druva
    om kända) till ett enda godkänn/avvisa-beslut.

    Normalfall: godkänn om varken producent- eller produktnamnskontrollen gav
    ett uttryckligt False (okänt/None räknas inte som motsägelse).

    Fallback: Systembolagets Producent-fält är ibland moderbolaget/importören,
    medan Vivino listar den faktiska undergården/märket för just den kuvén —
    och till skillnad från Dom Pérignon-fallet (där undermärket råkar synas i
    SB:s eget produktnamn) finns undermärkets namn ibland INGENSTANS i SB:s
    data alls. Upptäckt när "Tenuta della Famiglia Cecchi" (SB:s producent)
    inte delar ett enda ord med Vivinos "Val delle Rose" (Cecchis egen gård
    för just denna Morellino-kuvé) — producer_match ger då False (noll
    överlapp, ingen motsägelse), trots att det är rätt vin.

    Godkänns i så fall ENDAST om produktnamnet (det specifika, flerordiga
    kuvénamnet — en stark egen signal) OCH land bekräftar, OCH druvan inte
    uttryckligen MOTSÄGER (okänd druvdata räcker — upptäckt när "Grande
    Cuvée La Maison Rosé" saknade druvuppgift hos Systembolaget och därför
    aldrig kunde bekräftas, trots att producent-tystnaden annars var exakt
    samma mönster som "Val delle Rose"). Verifierad säker mot samtliga
    bekräftat FELAKTIGA träffar från den här sessionen (Viña Maipo/Cobos,
    St Stephan's Puttonyos/Esszencia, Crozes-Hermitage/Hermitage, Pierre
    Olivier/Morlet, Fat Bastard) — i varje sådant fall faller ÄVEN
    produktnamnet, eftersom fel-kandidaten faktiskt är ett annat specifikt
    vin."""
    baseline_valid = (producer_ok is not False) and (product_ok is not False)
    if baseline_valid:
        return True
    return producer_ok is False and product_ok is True and country_ok is True and grape_ok is not False


def _candidate_is_valid(wine: "Wine", details: dict) -> bool:
    """Kräver att INGET av de kända signalerna (land, druva) motsäger
    kandidaten, och att MINST ETT av dem faktiskt bekräftar den.

    Land ensamt räcker inte för producenter med många viner från samma land
    (upptäckt när Xavier Vignons ~10 olika franska Châteauneuf-du-Pape-kuvéer
    alla klarade land-kollen — fel kuvé valdes ändå eftersom druvan aldrig
    kontrollerades när landet redan "godkänt" kandidaten). Land-motsägelse
    diskvalificerar däremot direkt även om druvan råkar stämma (upptäckt när
    "Monterosso Rosso" från Italien matchades mot en kalifornisk Sangiovese
    av ren slump)."""
    vivino_country = details.get("country")
    country_known = bool(wine.ursprung) and bool(vivino_country)
    country_ok = country_match(wine.ursprung, vivino_country) if country_known else None

    vivino_grapes = details.get("grapes") or []
    grape_known = bool(wine.grapes) and bool(vivino_grapes)
    grape_ok = _dominant_grape_present(wine.grapes, vivino_grapes) if grape_known else None

    if country_known and not country_ok:
        return False
    if grape_known and not grape_ok:
        return False
    return bool(country_ok) or bool(grape_ok)


def find_validated_vertex_match(wine: "Wine", query_variants: list, project_number: str, engine_id: str,
                                 delay: float = 1.0, candidates_per_query: int = 3, max_variants: int = 2) -> Optional[dict]:
    """Provar flera sökfrågor (inte bara producent+namn) och flera kandidater
    per fråga (inte bara #1), och accepterar den första kandidaten som klarar
    _candidate_is_valid (land om känt på båda sidor, annars dominerande druva).

    Motiveras av två bekräftade felträffar: (1) en ren namnkollision
    ("Monterosso Rosso" → en orelaterad Napa-zinfandel) som en land-koll hade
    fångat direkt, och (2) en flerkuvé-producent (Xavier Vignon, ~10 olika
    Châteauneuf-du-Pape) där fel-kuvén rankades högst för frågan
    "producent+namn" men rätt (druv-matchande) vin låg etta för "namn" ensamt
    — dvs. att bara pröva den FÖRSTA frågevarianten missade det helt.

    Om ingen kandidat kan valideras (t.ex. Systembolaget saknar land/druv-
    data) faller vi tillbaka på allra första kandidaten från allra första
    frågan, samma beteende som innan denna funktion fanns."""
    first_seen = None
    for qi, q in enumerate(query_variants[:max_variants]):
        if qi > 0:
            time.sleep(delay)
        wine_ids = search_vertex_ai_for_vivino_wine(q, project_number, engine_id)
        for wine_id in wine_ids[:candidates_per_query]:
            details = get_vivino_wine_by_id(wine_id)
            time.sleep(delay)
            if not details:
                continue
            if first_seen is None:
                first_seen = details
            if _candidate_is_valid(wine, details):
                return details
    return first_seen


def enrich_with_vivino(wines: list, delay: float = 1.0,
                        google_api_key: Optional[str] = None, google_cx: Optional[str] = None,
                        vertex_project_number: Optional[str] = None, vertex_engine_id: Optional[str] = None) -> None:
    total = len(wines)
    for i, wine in enumerate(wines, 1):
        query_variants = _build_query_variants(wine)
        print(f"[{i}/{total}] Söker Vivino-betyg för: {query_variants[0] if query_variants else wine.namn}")

        best = None
        best_score = 0.0
        for qi, q in enumerate(query_variants):
            if qi > 0:
                time.sleep(delay)
            candidates = search_vivino(q)
            for c in candidates:
                p_sim = producer_similarity(wine.producent, c["winery"]) if wine.producent else 1.0
                n_sim = similarity(wine.namn, c["wine_name"]) if wine.namn else 1.0
                if p_sim < PRODUCER_SIM_THRESHOLD or n_sim < NAME_SIM_THRESHOLD:
                    continue
                combined = (p_sim + n_sim) / 2
                if combined > best_score:
                    best_score = combined
                    best = c
            if best:
                break  # godkänd träff hittad, ingen anledning att pröva fler varianter

        if best:
            wine.vivino_rating = best["rating"]
            wine.vivino_reviews = best["reviews"]
            wine.vivino_url = best["url"]
            wine.match_confidence = round(best_score, 2)
            wine.match_source = "vivino-sök"
        time.sleep(delay)  # var snäll mot Vivinos servrar

        have_fallback = (vertex_project_number and vertex_engine_id) or (google_api_key and google_cx)
        if wine.vivino_rating is None and have_fallback and query_variants:
            # Fallback: Vivinos egen sökning hittade ingen godkänd träff. Googles
            # index av vivino.com har visat sig hitta nya/småskaliga viner som
            # Vivinos egen (popularitetsviktade) sökrankning begraver. Vi litar på
            # extern sökrankning snarare än att köra fuzzy-matchning på nytt, men
            # provar flera frågevarianter och validerar mot land/druva (se
            # find_validated_vertex_match) — annars biter en enda felträff (fel
            # producent, eller fel kuvé hos en flerkuvé-producent) sig fast.
            details = None
            source = None
            if vertex_project_number and vertex_engine_id:
                details = find_validated_vertex_match(wine, query_variants, vertex_project_number,
                                                        vertex_engine_id, delay=delay)
                source = "vertex-ai-search"
            if not details and google_api_key and google_cx:
                wine_ids = search_google_for_vivino_wine(query_variants[0], google_api_key, google_cx)
                if wine_ids:
                    details = get_vivino_wine_by_id(wine_ids[0])
                source = "google-cse"
            if details:
                wine.vivino_rating = details["rating"]
                wine.vivino_reviews = details["reviews"]
                wine.vivino_url = details["url"]
                wine.match_source = source
            time.sleep(delay)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_csv(wines: list, path: str) -> None:
    wines_sorted = sorted(
        wines,
        key=lambda w: (w.vivino_rating is None, -(w.vivino_rating or 0))
    )
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Varunummer", "Namn", "Producent", "Pris", "Volym", "Ursprung", "Druvor",
            "Vivino_betyg", "Vivino_antal_recensioner", "Matchningssäkerhet", "Matchningskälla", "Vivino_url"
        ])
        for w in wines_sorted:
            writer.writerow([
                w.varunummer, w.namn, w.producent, w.pris, w.volym, w.ursprung, w.grapes,
                w.vivino_rating, w.vivino_reviews, w.match_confidence, w.match_source, w.vivino_url
            ])
    print(f"\nKlart! Resultat sparat i: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Korskör Systembolagets vinsortiment mot Vivino-betyg")
    parser.add_argument("--source", choices=["csv", "store"], required=True,
                         help="'csv' = läs sortiment från nedladdad fil, 'store' = hämta live från en butik (experimentellt)")
    parser.add_argument("--file", help="Sökväg till CSV-fil (krävs om --source csv)")
    parser.add_argument("--store-id", help="Systembolagets butiks-ID (krävs om --source store)")
    parser.add_argument("--api-key", default=SYSTEMBOLAGET_API_KEY_PLACEHOLDER,
                         help="API-nyckel för Systembolagets sök-API (endast för --source store)")
    parser.add_argument("--limit", type=int, default=None,
                         help="Max antal viner att slå upp på Vivino (bra för att testa snabbt, t.ex. --limit 10)")
    parser.add_argument("--output", default="wines_with_ratings.csv", help="Namn på output-fil")
    parser.add_argument("--delay", type=float, default=1.0, help="Sekunder mellan Vivino-anrop (var snäll mot deras servrar)")
    parser.add_argument("--assortment", default=None,
                         help="Kommaseparerad lista på Systembolagets 'assortmentText'-värden att begränsa till "
                              "(endast --source store), t.ex. 'Tillfälligt sortiment,Säsong'. Standard: alla.")
    parser.add_argument("--category2", default=",".join(sorted(WINE_CATEGORIES)),
                         help="Kommaseparerad lista på Systembolagets 'categoryLevel2'-värden att begränsa till "
                              "(endast --source store). Standard: de fyra 'riktiga' vinkategorierna (rött/vitt/"
                              "mousserande/rosé) — exkluderar t.ex. Glögg, Vermouth, Sake, Starkvin, Aperitifer. "
                              "Ange en tom sträng (--category2 '') för att inte filtrera alls.")
    parser.add_argument("--google-api-key", default=os.environ.get("GOOGLE_API_KEY"),
                         help="API-nyckel för Google Custom Search JSON API. Används som fallback (kostar pengar "
                              "utöver 100 sökningar/dag gratis) för viner där Vivinos egen sökning inte gav någon "
                              "godkänd träff. Standard: miljövariabeln GOOGLE_API_KEY.")
    parser.add_argument("--google-cx", default=os.environ.get("GOOGLE_CSE_ID"),
                         help="Search engine ID (cx) för en Google Programmable Search Engine avgränsad till "
                              "vivino.com. Standard: miljövariabeln GOOGLE_CSE_ID.")
    parser.add_argument("--vertex-project-number", default=os.environ.get("VERTEX_PROJECT_NUMBER"),
                         help="Google Cloud-projektnummer för Vertex AI Search-fallback (används istället för "
                              "Google Custom Search, som är stängt för nya konton). Kräver att `gcloud auth login` "
                              "körts lokalt. Standard: miljövariabeln VERTEX_PROJECT_NUMBER.")
    parser.add_argument("--vertex-engine-id", default=os.environ.get("VERTEX_ENGINE_ID"),
                         help="Engine-ID för en Vertex AI Search-app (basic website search avgränsad till "
                              "vivino.com). Standard: miljövariabeln VERTEX_ENGINE_ID.")
    args = parser.parse_args()

    if args.source == "csv":
        if not args.file:
            print("--file krävs när --source csv används")
            sys.exit(1)
        wines = load_from_csv(args.file)
    else:
        if not args.store_id:
            print("--store-id krävs när --source store används")
            sys.exit(1)
        include_assortment = set(a.strip() for a in args.assortment.split(",")) if args.assortment else None
        include_category2 = set(a.strip() for a in args.category2.split(",")) if args.category2 else None
        wines = load_from_store(args.store_id, args.api_key, include_assortment=include_assortment,
                                 include_category2=include_category2)

    print(f"Hittade {len(wines)} viner i sortimentet.")

    if args.limit:
        wines = wines[:args.limit]
        print(f"Begränsar till {len(wines)} viner (--limit).")

    if args.vertex_project_number and args.vertex_engine_id:
        print("Vertex AI Search-fallback aktiverad.")
    elif args.google_api_key and args.google_cx:
        print("Google Custom Search-fallback aktiverad (kostar pengar utöver gratiskvoten på 100 sökningar/dag).")

    enrich_with_vivino(wines, delay=args.delay, google_api_key=args.google_api_key, google_cx=args.google_cx,
                        vertex_project_number=args.vertex_project_number, vertex_engine_id=args.vertex_engine_id)
    write_csv(wines, args.output)


if __name__ == "__main__":
    main()
