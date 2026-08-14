"""Hämtar Vivinos marknadspris (kr/750ml, normaliserat för flaskstorlek) för
varje vin i vivino_matches.csv. Skriver data/vivino_prices.csv — skrivs över
varje körning, precis som all_stores_wines.csv, eftersom priser (till
skillnad från betyg) är färskvara.

De flesta vardagsviner saknar aktiva återförsäljare på Vivino och får ingen
rad här — det är förväntat, inte ett fel (se docstring i
get_vivino_market_price_per_750ml).

Körs i data/-mappen: python ../scripts/fetch_vivino_prices.py
"""
import csv
import sys
import time

sys.path.insert(0, ".")
from systembolaget_vivino import _VIVINO_WINE_ID_RE, get_vivino_market_price_per_750ml

IN_CSV = "vivino_matches.csv"
OUT_CSV = "vivino_prices.csv"
FIELDNAMES = ["Varunummer", "Vivino_pris_per_750ml", "Vivino_prispunkter", "Vivino_prisargang"]

with open(IN_CSV, encoding="utf-8-sig") as f:
    matches = [r for r in csv.DictReader(f) if r.get("Vivino_url", "").strip()]

print(f"{len(matches)} matchade viner att kolla pris för.", flush=True)

out_rows = []
found = 0
for i, r in enumerate(matches, 1):
    m = _VIVINO_WINE_ID_RE.search(r["Vivino_url"])
    if not m:
        continue
    result = get_vivino_market_price_per_750ml(m.group(1))
    if result:
        out_rows.append({
            "Varunummer": r["Varunummer"],
            "Vivino_pris_per_750ml": result["pris_per_750ml"],
            "Vivino_prispunkter": result["prispunkter"],
            "Vivino_prisargang": result["argang"],
        })
        found += 1
    if i % 100 == 0:
        print(f"[{i}/{len(matches)}] {found} priser hittade hittills", flush=True)
    time.sleep(1.0)

with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=FIELDNAMES)
    w.writeheader()
    w.writerows(out_rows)

print(f"Klart. {found}/{len(matches)} viner hade ett Vivino-marknadspris. Skrivet till {OUT_CSV}.")
