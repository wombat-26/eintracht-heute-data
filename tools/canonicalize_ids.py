#!/usr/bin/env python3
"""Bringt die Match-IDs im Seed auf die kanonische Form JJJJMMTT-heim-gast.

Warum das noetig ist: 215 IDs sind historisch gewachsen und stimmen nicht mit
slug(Teamname) ueberein - "FC Bayern München" steht als "bayernmn" statt
"fcbayern" (74 Spiele), Werder Bremen als "svwerder" (42), Schalke als
"fcschalk" (34). Der App-Sync und diese Pipeline bilden die ID aber aus den
normalisierten Teamnamen und treffen damit die kanonische Form. Ergebnis:
Jedes gesyncte Bayern-Spiel landet als ZWEITER Datensatz auf dem Geraet.

Die Migrationen v4/v5 in der App raeumen das auf, aber nur einmalig - die
Ursache erzeugt bei jedem Sync neue Dubletten. Deshalb hier an der Wurzel.

EINMALIG auszufuehren, danach ist der Seed dauerhaft konsistent:

    python3 canonicalize_ids.py ../data/seed_matches.json

Das Skript bricht ab, wenn die Umbenennung zwei Spiele auf dieselbe ID
abbilden wuerde. Gegen den aktuellen Bestand geprueft: null Kollisionen.

Auf dem Geraet verwaisen dadurch die alten Datensaetze - MatchStore
.pruneIdVarianten() loest das nach dem naechsten Seed-Import auf.
"""
import json, sys, argparse, collections
from seedkit import slug, validate


def kanonische_id(m):
    return f"{m['date'][:10].replace('-', '')}-{slug(m['homeTeam'])}-{slug(m['awayTeam'])}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seed")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = json.load(open(args.seed, encoding="utf-8"))
    ms = doc["matches"]

    abweichend = [m for m in ms if m["id"] != kanonische_id(m)]
    print(f"{len(ms)} Spiele, davon {len(abweichend)} mit abweichender ID")
    if not abweichend:
        print("Bereits kanonisch – nichts zu tun.")
        return 0

    # Kollisionspruefung VOR jeder Aenderung
    neue = collections.Counter(kanonische_id(m) for m in ms)
    kollisionen = [k for k, v in neue.items() if v > 1]
    if kollisionen:
        print(f"ABBRUCH: {len(kollisionen)} IDs waeren doppelt:")
        for k in kollisionen[:10]:
            for m in ms:
                if kanonische_id(m) == k:
                    print(f"  {k}  <- {m['id']}  {m['homeTeam']} – {m['awayTeam']}")
        return 1

    haeufig = collections.Counter()
    for m in abweichend:
        alt, neu = m["id"].split("-"), kanonische_id(m).split("-")
        if alt[1] != neu[1]:
            haeufig[(m["homeTeam"], alt[1], neu[1])] += 1
        if alt[2] != neu[2]:
            haeufig[(m["awayTeam"], alt[2], neu[2])] += 1
    print(f"\n{'Verein':32s} {'alt':10s} {'neu':10s} n")
    for (t, a, n), c in haeufig.most_common():
        print(f"  {t:30s} {a:10s} {n:10s} {c}")

    if args.dry_run:
        print("\n--dry-run: nichts geschrieben.")
        return 0

    for m in ms:
        m["id"] = kanonische_id(m)

    fehler, _ = validate(ms)
    if fehler:
        print(f"ABBRUCH: {len(fehler)} Validierungsfehler nach der Umbenennung.")
        for e in fehler[:5]:
            print("  ", e)
        return 1

    ms.sort(key=lambda m: (m["date"], m["id"]))
    with open(args.seed, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n{len(abweichend)} IDs kanonisiert, {len(ms)} Spiele geschrieben.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
