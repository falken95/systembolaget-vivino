"""Bygger master_wines.csv genom att slå ihop all_stores_wines.csv (hela
sortimentet, butikskopplat) med Vivino-matchningarna i vivino_matches.csv och
Vivino-marknadspriserna i vivino_prices.csv (saknas för de flesta vardagsviner
— se fetch_vivino_prices.py). Viner utan matchning/prisdata får tomma fält."""
import csv
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
              "Antal_butiker", "Butiker", "Vivino_url", "Vivino_betyg", "Vivino_antal_recensioner",
              "Vivino_pris_per_750ml", "Vivino_prispunkter", "Rabatt_procent"]

out_rows = []
matched_count = 0
priced_count = 0
for r in base_rows:
    m = matches.get(r["Varunummer"])
    if m:
        matched_count += 1
    row = {k: r.get(k, "") for k in fieldnames if k not in (
        "Vivino_url", "Vivino_betyg", "Vivino_antal_recensioner",
        "Vivino_pris_per_750ml", "Vivino_prispunkter", "Rabatt_procent")}
    row["Vivino_url"] = m["Vivino_url"] if m else ""
    row["Vivino_betyg"] = m["Vivino_betyg"] if m else ""
    row["Vivino_antal_recensioner"] = m["Vivino_antal_recensioner"] if m else ""

    p = prices.get(r["Varunummer"])
    sb_vol_ml = parse_sb_volume_ml(r.get("Volym"))
    sb_pris = float(r["Pris"]) if r.get("Pris") not in (None, "") else None
    rabatt_procent = ""
    if p and sb_vol_ml and sb_pris is not None:
        priced_count += 1
        vivino_pris = float(p["Vivino_pris_per_750ml"])
        sb_pris_per_750 = sb_pris / (sb_vol_ml / 750)
        rabatt_procent = round((vivino_pris - sb_pris_per_750) / vivino_pris * 100)
    row["Vivino_pris_per_750ml"] = p["Vivino_pris_per_750ml"] if p else ""
    row["Vivino_prispunkter"] = p["Vivino_prispunkter"] if p else ""
    row["Rabatt_procent"] = rabatt_procent
    out_rows.append(row)

with open("master_wines.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(out_rows)

print(f"Total: {len(out_rows)}, matched: {matched_count} ({matched_count/len(out_rows)*100:.1f}%), "
      f"med Vivino-pris: {priced_count}")
