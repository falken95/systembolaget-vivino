"""Helper for the national WebSearch matching run. Imports the existing,
already-validated matching functions from systembolaget_vivino.py — does NOT
reimplement any matching logic. Only handles: loading input, checking what's
already in the results CSV (for resume), fetching Vivino wine details by id,
running candidate_accepted, and appending CSV rows."""
import csv
import json
import os
import sys
import time

sys.path.insert(0, r"C:\Users\carlp\projects\systembolaget-vivino")
from systembolaget_vivino import (
    get_vivino_wine_by_id,
    producer_match,
    distinctive_words_subset_match,
    country_match,
    _dominant_grape_present,
    candidate_accepted,
    _PRODUCT_NAME_STOPWORDS,
)

INPUT_PATH = r"C:\Users\carlp\projects\systembolaget-vivino\national_to_match.json"
OUTPUT_PATH = r"C:\Users\carlp\projects\systembolaget-vivino\national_match_results.csv"

CSV_FIELDS = [
    "Varunummer", "Namn", "Producent", "Ursprung", "Druvor", "Status",
    "Funnen_Vivino_url", "Vivino_winery", "Vivino_wine_name", "Vivino_betyg",
    "Vivino_antal_recensioner", "Producer_check", "Product_check", "Country_ok",
    "Grape_ok", "Systembolaget_url",
]


def load_wines():
    with open(INPUT_PATH, encoding="utf-8") as f:
        return json.load(f)


def already_done():
    if not os.path.exists(OUTPUT_PATH):
        return set()
    done = set()
    with open(OUTPUT_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            done.add(str(row["Varunummer"]))
    return done


def ensure_header():
    if not os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def append_rows(rows):
    ensure_header()
    with open(OUTPUT_PATH, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        for row in rows:
            writer.writerow(row)


def sb_url(varunummer):
    return f"https://www.systembolaget.se/produkt/vin/-{varunummer}/"


def evaluate_candidate(wine, wine_id):
    """Fetch a candidate Vivino wine by id and run it through the existing
    validated checks. Returns (accepted: bool, details: dict|None, checks: dict)."""
    details = get_vivino_wine_by_id(wine_id)
    time.sleep(0.3)
    if not details:
        return False, None, {}
    producer_ok = producer_match(wine["Producent"], wine["Namn"], details.get("winery") or "")
    product_ok = distinctive_words_subset_match(
        wine["Namn"], f"{details.get('winery') or ''} {details.get('wine_name') or ''}",
        _PRODUCT_NAME_STOPWORDS,
    )
    country_ok = country_match(wine.get("Ursprung"), details.get("country"))
    grape_ok = _dominant_grape_present(wine.get("Druvor"), details.get("grapes") or [])
    accepted = candidate_accepted(producer_ok, product_ok, country_ok, grape_ok)
    checks = {
        "producer_ok": producer_ok,
        "product_ok": product_ok,
        "country_ok": country_ok,
        "grape_ok": grape_ok,
    }
    return accepted, details, checks


def make_matched_row(wine, details, checks):
    return {
        "Varunummer": wine["Varunummer"],
        "Namn": wine["Namn"],
        "Producent": wine["Producent"],
        "Ursprung": wine.get("Ursprung", ""),
        "Druvor": wine.get("Druvor", ""),
        "Status": "Matchad",
        "Funnen_Vivino_url": details.get("url"),
        "Vivino_winery": details.get("winery"),
        "Vivino_wine_name": details.get("wine_name"),
        "Vivino_betyg": details.get("rating"),
        "Vivino_antal_recensioner": details.get("reviews"),
        "Producer_check": checks.get("producer_ok"),
        "Product_check": checks.get("product_ok"),
        "Country_ok": checks.get("country_ok"),
        "Grape_ok": checks.get("grape_ok"),
        "Systembolaget_url": sb_url(wine["Varunummer"]),
    }


def make_unmatched_row(wine):
    return {
        "Varunummer": wine["Varunummer"],
        "Namn": wine["Namn"],
        "Producent": wine["Producent"],
        "Ursprung": wine.get("Ursprung", ""),
        "Druvor": wine.get("Druvor", ""),
        "Status": "Ej matchad",
        "Funnen_Vivino_url": "",
        "Vivino_winery": "",
        "Vivino_wine_name": "",
        "Vivino_betyg": "",
        "Vivino_antal_recensioner": "",
        "Producer_check": "",
        "Product_check": "",
        "Country_ok": "",
        "Grape_ok": "",
        "Systembolaget_url": sb_url(wine["Varunummer"]),
    }
