"""Nationsomfattande (ingen storeId) hämtning av hela vinsortimentet, för att
1) backfilla ProductLaunchDate/IsNewInAssortment (produktnivå-attribut) och
2) fånga HELA Ordervaror-sortimentet (online) komplett.

Segmenterad per AssortmentText (och för Ordervaror ytterligare per
CategoryLevel2) eftersom Systembolagets sök-API har ett paginerings-tak på
~333 sidor (~9990 träffar) — därefter upprepar den bara sista sidan istället
för att signalera slut, så en enda odelad fråga för hela sortimentet (15 452
träffar) eller för Ordervaror ensamt (>10 000) missar data tyst. Rätt
sluttecken är metadata.nextPage == -1, INTE "tom produktlista"."""
import csv
import sys
import time

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from systembolaget_vivino import (SYSTEMBOLAGET_SEARCH_URL, SYSTEMBOLAGET_API_KEY_PLACEHOLDER,
                                   REQUEST_HEADERS, WINE_CATEGORIES)

EXCLUDE_PACKAGING = {"Box", "Påse", "Pappförpackning", "PET-flaska"}

headers = dict(REQUEST_HEADERS)
headers["Ocp-Apim-Subscription-Key"] = SYSTEMBOLAGET_API_KEY_PLACEHOLDER

national = {}  # Varunummer -> product dict


def crawl_segment(extra_params, label):
    page = 0
    added = 0
    while True:
        params = {"categoryLevel1": "Vin", "page": page, "size": 30, **extra_params}
        try:
            resp = requests.get(SYSTEMBOLAGET_SEARCH_URL, headers=headers, params=params, timeout=20)
        except requests.RequestException as e:
            print(f"[{label}] nätverksfel sida {page}: {e}, försöker igen om 3s", flush=True)
            time.sleep(3)
            continue
        if resp.status_code != 200:
            print(f"[{label}] fel status {resp.status_code} sida {page}, avbryter segment", flush=True)
            break
        data = resp.json()
        products = data.get("products", [])
        if not products:
            break
        for p in products:
            if p.get("categoryLevel2") not in WINE_CATEGORIES:
                continue
            if p.get("packagingLevel1") in EXCLUDE_PACKAGING:
                continue
            if p.get("categoryLevel3") == "Smaksatt":
                continue
            vn = str(p.get("productNumber", ""))
            if vn not in national:
                added += 1
            national[vn] = {
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
                "IsNewInAssortment": p.get("isNewInAssortment"),
                "ProductLaunchDate": (p.get("productLaunchDate") or "").split("T")[0],
            }
        # RÄTT sluttecken: metadata.nextPage == -1, inte "tom produktlista"
        # (API:t upprepar annars bara sista sidan för evigt efter ~333 sidor).
        if data.get("metadata", {}).get("nextPage", -1) == -1:
            break
        page += 1
        time.sleep(0.2)
        if page > 500:
            print(f"[{label}] skyddsstopp vid 500 sidor (borde inte hända nu)", flush=True)
            break
    print(f"[{label}] klar: {page + 1} sidor, {added} nya viner (totalt {len(national)} hittills)", flush=True)


# Segment 1-5: alla sortiment utom Ordervaror (redan under 10k-taket var för sig)
for assortment in ["Fast sortiment", "Tillfälligt sortiment", "Lokalt & Småskaligt", "Säsong", "Webblanseringar"]:
    crawl_segment({"assortmentText": assortment}, assortment)

# Segment 6-9: Ordervaror ensamt är >10k, dela per druvtyp (varje del under taket)
for cat2 in WINE_CATEGORIES:
    crawl_segment({"assortmentText": "Ordervaror", "categoryLevel2": cat2}, f"Ordervaror/{cat2}")

print(f"\nKlart. {len(national)} unika viner totalt (alla sortimentstyper).", flush=True)

fieldnames = ["Varunummer", "Namn", "Producent", "Pris", "Volym", "Forpackning", "Kategori3",
              "Ursprung", "Druvor", "AssortmentText", "IsNewInAssortment", "ProductLaunchDate"]
with open("national_backfill.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(national.values())

from collections import Counter
print(Counter(v["AssortmentText"] for v in national.values()))
