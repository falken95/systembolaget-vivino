"""Bygger master_wines.csv genom att slå ihop all_stores_wines.csv (hela
sortimentet, butikskopplat) med Vivino-matchningarna i vivino_matches.csv.
Viner utan matchning (t.ex. ännu inte matchade online-only-viner) får tomma
Vivino-fält."""
import csv

with open("vivino_matches.csv", encoding="utf-8-sig") as f:
    matches = {r["Varunummer"]: r for r in csv.DictReader(f)}

with open("all_stores_wines.csv", encoding="utf-8-sig") as f:
    base_rows = list(csv.DictReader(f))

fieldnames = ["Varunummer", "Namn", "Producent", "Pris", "Volym", "Forpackning", "Kategori3",
              "Ursprung", "Druvor", "AssortmentText", "IsNewInAssortment", "ProductLaunchDate",
              "Antal_butiker", "Butiker", "Vivino_url", "Vivino_betyg", "Vivino_antal_recensioner"]

out_rows = []
matched_count = 0
for r in base_rows:
    m = matches.get(r["Varunummer"])
    if m:
        matched_count += 1
    row = {k: r.get(k, "") for k in fieldnames if k not in ("Vivino_url", "Vivino_betyg", "Vivino_antal_recensioner")}
    row["Vivino_url"] = m["Vivino_url"] if m else ""
    row["Vivino_betyg"] = m["Vivino_betyg"] if m else ""
    row["Vivino_antal_recensioner"] = m["Vivino_antal_recensioner"] if m else ""
    out_rows.append(row)

with open("master_wines.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(out_rows)

print(f"Total: {len(out_rows)}, matched: {matched_count} ({matched_count/len(out_rows)*100:.1f}%)")
