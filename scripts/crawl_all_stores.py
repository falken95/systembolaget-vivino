"""Fullskalig körning: hämtar vinsortimentet (med våra etablerade
kvalifikationer) för ALLA fysiska butiker (isAgent=false, exkl. ombud) och
bygger en Varunummer -> lista av butiks-ID:n-mappning. Resumable: skriver
efter VARJE butik, och hoppar över redan klara butiker vid omstart."""
import csv
import json
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from systembolaget_vivino import (SYSTEMBOLAGET_SEARCH_URL, SYSTEMBOLAGET_API_KEY_PLACEHOLDER,
                                   REQUEST_HEADERS, WINE_CATEGORIES)

EXCLUDE_PACKAGING = {"Box", "Påse", "Pappförpackning", "PET-flaska"}
OUT_CSV = "all_stores_wines.csv"
PROGRESS_FILE = "all_stores_progress.json"
FIELDNAMES = ["Varunummer", "Namn", "Producent", "Pris", "Volym", "Forpackning", "Kategori3",
              "Ursprung", "Druvor", "AssortmentText", "Antal_butiker", "Butiker"]

headers = dict(REQUEST_HEADERS)
headers["Ocp-Apim-Subscription-Key"] = SYSTEMBOLAGET_API_KEY_PLACEHOLDER

# --- Hämta butikslistan ---
resp = requests.get("https://raw.githubusercontent.com/AlexGustafsson/systembolaget-api-data/main/data/stores.json", timeout=30)
all_sites = resp.json()
stores = [s for s in all_sites if not s.get("isAgent") and s.get("isOpen") and not s.get("isBlocked")]
print(f"{len(stores)} fysiska butiker att gå igenom.", flush=True)

# --- Ladda ev. tidigare progress ---
wines = {}  # Varunummer -> {fields..., "Butiker": set(siteId)}
done_stores = set()
try:
    with open(PROGRESS_FILE, encoding="utf-8") as f:
        done_stores = set(json.load(f))
    with open(OUT_CSV, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            vn = row["Varunummer"]
            wines[vn] = {k: row[k] for k in FIELDNAMES if k not in ("Antal_butiker", "Butiker")}
            wines[vn]["Butiker"] = set(row["Butiker"].split(",")) if row["Butiker"] else set()
    print(f"Återupptar: {len(done_stores)} butiker redan klara, {len(wines)} viner inlästa.", flush=True)
except FileNotFoundError:
    pass


def save_progress():
    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for vn, data in wines.items():
            row = {k: v for k, v in data.items() if k != "Butiker"}
            row["Antal_butiker"] = len(data["Butiker"])
            row["Butiker"] = ",".join(sorted(data["Butiker"]))
            w.writerow(row)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(done_stores), f)


remaining = [s for s in stores if s["siteId"] not in done_stores]
print(f"{len(remaining)} butiker kvar att hämta.", flush=True)

for i, store in enumerate(remaining, 1):
    site_id = store["siteId"]
    page = 0
    raw_count = 0
    kept_count = 0
    while True:
        params = {
            "categoryLevel1": "Vin",
            "storeId": site_id,
            "isInStoreAssortmentSearch": "true",
            "page": page,
            "size": 30,
        }
        try:
            resp = requests.get(SYSTEMBOLAGET_SEARCH_URL, headers=headers, params=params, timeout=20)
        except requests.RequestException as e:
            print(f"[{site_id}] nätverksfel sida {page}: {e}, försöker igen om 3s", flush=True)
            time.sleep(3)
            continue
        if resp.status_code == 404 and page > 0:
            break
        if resp.status_code != 200:
            print(f"[{site_id}] fel status {resp.status_code} sida {page}", flush=True)
            break
        products = resp.json().get("products", [])
        if not products:
            break
        for p in products:
            raw_count += 1
            if p.get("categoryLevel2") not in WINE_CATEGORIES:
                continue
            if p.get("packagingLevel1") in EXCLUDE_PACKAGING:
                continue
            if p.get("categoryLevel3") == "Smaksatt":
                continue
            vn = str(p.get("productNumber", ""))
            if vn not in wines:
                wines[vn] = {
                    "Varunummer": vn,
                    "Namn": f"{p.get('productNameBold') or ''} {p.get('productNameThin') or ''}".strip(),
                    "Producent": p.get("producerName", ""),
                    "Pris": p.get("price"),
                    "Volym": p.get("volumeText", ""),
                    "Forpackning": p.get("packagingLevel1", ""),
                    "Kategori3": p.get("categoryLevel3", ""),
                    "Ursprung": p.get("country", ""),
                    "Druvor": ", ".join(p.get("grapes") or []),
                    "AssortmentText": p.get("assortmentText", ""),
                    "Butiker": set(),
                }
            wines[vn]["Butiker"].add(site_id)
            kept_count += 1
        page += 1
        time.sleep(0.25)
        if page > 80:
            print(f"[{site_id}] avbryter efter 80 sidor (skyddsstopp)", flush=True)
            break

    done_stores.add(site_id)
    save_progress()
    print(f"[{i}/{len(remaining)}] {site_id} {store['displayName']} ({store['city']}): "
          f"{raw_count} rådata, {kept_count} viner. Totalt unika viner hittills: {len(wines)}", flush=True)

print(f"\nKlart. {len(wines)} unika viner över {len(done_stores)} butiker. Skrivet till {OUT_CSV}.", flush=True)
