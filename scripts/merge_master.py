"""Bygger master_wines.csv genom att slå ihop all_stores_wines.csv (hela
sortimentet, butikskopplat) med Vivino-matchningarna i vivino_matches.csv och
Vivino-marknadspriserna i vivino_prices.csv (saknas för de flesta vardagsviner
— se fetch_vivino_prices.py). Viner utan matchning/prisdata får tomma fält.

Viner som försvinner ur en crawl (utgångna/cykla ut) TAS INTE BORT — de
behålls i master_wines.csv med Aktiv=False (dolda i appen av build_vinguide.py)
istället för att droppas, så att om de dyker upp igen senare behövs ingen ny
WebSearch-matchning (deras Vivino_url finns redan kvar i vivino_matches.csv
och deras rad återaktiveras bara med Aktiv=True och färsk data från crawlen).
all_stores_wines.csv självt är fortfarande en ren nulägessnapshot (skrivs över
varje körning) — det är HÄR, i den kumulativa master_wines.csv, som historiken
bevaras."""
import csv
import os
import sys

sys.path.insert(0, ".")
from systembolaget_vivino import parse_sb_volume_ml

with open("vivino_matches.csv", encoding="utf-8-sig") as f:
    matches = {r["Varunummer"]: r for r in csv.DictReader(f)}

with open("vivino_prices.csv", encoding="utf-8-sig") as f:
    prices = {r["Varunummer"]: r for r in csv.DictReader(f)}

with open("all_stores_wines.csv", encoding="utf-8-sig") as f:
    base_rows = list(csv.DictReader(f))

fieldnames = ["Varunummer", "Namn", "Producent", "Pris", "Volym", "Forpackning", "Kategori3",
              "Ursprung", "Druvor", "AssortmentText", "IsNewInAssortment", "ProductLaunchDate", "Argang",
              "IsCompletelyOutOfStock", "IsTemporaryOutOfStock", "IsDiscontinued", "IsSupplierTemporaryNotAvailable",
              "Antal_butiker", "Butiker", "Vivino_url", "Vivino_betyg", "Vivino_antal_recensioner",
              "Vivino_pris_per_750ml", "Vivino_prispunkter", "Rabatt_procent", "Aktiv"]

SB_FIELDS = ["Varunummer", "Namn", "Producent", "Pris", "Volym", "Forpackning", "Kategori3",
             "Ursprung", "Druvor", "AssortmentText", "IsNewInAssortment", "ProductLaunchDate", "Argang",
             "IsCompletelyOutOfStock", "IsTemporaryOutOfStock", "IsDiscontinued", "IsSupplierTemporaryNotAvailable",
             "Antal_butiker", "Butiker"]

previous_master = {}
if os.path.exists("master_wines.csv"):
    with open("master_wines.csv", encoding="utf-8-sig") as f:
        previous_master = {r["Varunummer"]: r for r in csv.DictReader(f)}


def vivino_fields(vn):
    m = matches.get(vn)
    return {
        "Vivino_url": m["Vivino_url"] if m else "",
        "Vivino_betyg": m["Vivino_betyg"] if m else "",
        "Vivino_antal_recensioner": m["Vivino_antal_recensioner"] if m else "",
    }


def price_fields(vn, sb_pris_str, sb_volym_str):
    p = prices.get(vn)
    sb_vol_ml = parse_sb_volume_ml(sb_volym_str)
    sb_pris = float(sb_pris_str) if sb_pris_str not in (None, "") else None
    rabatt_procent = ""
    priced = False
    if p and sb_vol_ml and sb_pris is not None:
        priced = True
        vivino_pris = float(p["Vivino_pris_per_750ml"])
        sb_pris_per_750 = sb_pris / (sb_vol_ml / 750)
        rabatt_procent = round((vivino_pris - sb_pris_per_750) / vivino_pris * 100)
    return {
        "Vivino_pris_per_750ml": p["Vivino_pris_per_750ml"] if p else "",
        "Vivino_prispunkter": p["Vivino_prispunkter"] if p else "",
        "Rabatt_procent": rabatt_procent,
    }, priced


out_rows = {}
matched_count = 0
priced_count = 0

# Aktiva viner: färsk data från den här körningens all_stores_wines.csv.
for r in base_rows:
    vn = r["Varunummer"]
    row = {k: r.get(k, "") for k in SB_FIELDS}
    row.update(vivino_fields(vn))
    if matches.get(vn):
        matched_count += 1
    pf, priced = price_fields(vn, r.get("Pris"), r.get("Volym"))
    row.update(pf)
    if priced:
        priced_count += 1
    row["Aktiv"] = "True"
    out_rows[vn] = row

# Utgångna viner: fanns i förra veckans master men inte i den här crawlen.
# Behålls med senast kända Systembolaget-data, Aktiv=False (döljs i appen),
# men Vivino-fälten uppdateras ifall matchningen/priset ändrats sedan dess.
cycled_out_count = 0
for vn, old in previous_master.items():
    if vn in out_rows:
        continue
    row = {k: old.get(k, "") for k in SB_FIELDS}
    row.update(vivino_fields(vn))
    if matches.get(vn):
        matched_count += 1
    pf, priced = price_fields(vn, old.get("Pris"), old.get("Volym"))
    row.update(pf)
    if priced:
        priced_count += 1
    row["Aktiv"] = "False"
    out_rows[vn] = row
    cycled_out_count += 1

with open("master_wines.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(out_rows.values())

print(f"Total: {len(out_rows)} ({len(base_rows)} aktiva, {cycled_out_count} utgångna/dolda), "
      f"matched: {matched_count} ({matched_count/len(out_rows)*100:.1f}%), med Vivino-pris: {priced_count}")
