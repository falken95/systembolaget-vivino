"""Engångsscript: slår ihop wines_full_store.csv (gammalt schema, Brommaplan)
och national_match_results.csv (nytt schema, övriga butiker) till en enda
kanonisk data/vivino_matches.csv. Framtida veckovisa matchningar av nya viner
ska läggas till (append) i just den filen, med samma kolumner."""
import csv

FIELDNAMES = ["Varunummer", "Vivino_url", "Vivino_betyg", "Vivino_antal_recensioner", "matched_date"]

matches = {}

with open("wines_full_store.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r.get("Vivino_url", "").strip():
            matches[r["Varunummer"]] = {
                "Varunummer": r["Varunummer"],
                "Vivino_url": r["Vivino_url"],
                "Vivino_betyg": r["Vivino_betyg"],
                "Vivino_antal_recensioner": r["Vivino_antal_recensioner"],
                "matched_date": "",
            }

with open("national_match_results.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if r.get("Status") == "Matchad" and r.get("Funnen_Vivino_url", "").strip():
            matches[r["Varunummer"]] = {
                "Varunummer": r["Varunummer"],
                "Vivino_url": r["Funnen_Vivino_url"],
                "Vivino_betyg": r["Vivino_betyg"],
                "Vivino_antal_recensioner": r["Vivino_antal_recensioner"],
                "matched_date": "",
            }

with open("vivino_matches.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=FIELDNAMES)
    w.writeheader()
    w.writerows(matches.values())

print(f"Konsoliderade {len(matches)} matchade viner till vivino_matches.csv")
