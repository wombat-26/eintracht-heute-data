#!/usr/bin/env python3
"""Validiert seed_matches.json gegen das Swift-Datenmodell.

Exit 0 = sauber, Exit 1 = Fehler (Action bricht ab, kein Commit).

Der Validator existiert wegen SeedLoader.load(): dort dekodiert
`try? decoder.decode(SeedFile.self, ...)` nicht lossy. Ein einziger
unbekannter competition-Wert oder Typfehler laesst die Funktion []
zurueckgeben - eine Neuinstallation startet dann mit leerer Datenbank,
ohne sichtbaren Fehler. Deshalb: lieber ein roter Job als eine
stillschweigend kaputte Auslieferung.
"""
import json, sys, argparse
from seedkit import validate, content_hash

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seed")
    ap.add_argument("--strict", action="store_true",
                    help="Warnungen ebenfalls als Fehler werten")
    ap.add_argument("--min-count", type=int, default=None,
                    help="Abbruch, wenn weniger Spiele als angegeben")
    args = ap.parse_args()

    try:
        doc = json.load(open(args.seed, encoding="utf-8"))
    except Exception as e:
        print(f"FEHLER: {args.seed} ist kein gueltiges JSON: {e}")
        return 1

    if not isinstance(doc, dict) or "matches" not in doc:
        print("FEHLER: Wurzelobjekt braucht den Schluessel 'matches'")
        return 1

    matches = doc["matches"]
    errs, warns = validate(matches)

    print(f"{args.seed}: {len(matches)} Spiele")
    print(f"  Fehler:     {len(errs)}")
    print(f"  Warnungen:  {len(warns)}")
    print(f"  contentHash {content_hash(matches)}")

    for e in errs[:50]:
        print(f"  E: {e}")
    if len(errs) > 50:
        print(f"  ... und {len(errs)-50} weitere Fehler")
    for w in warns[:20]:
        print(f"  W: {w}")
    if len(warns) > 20:
        print(f"  ... und {len(warns)-20} weitere Warnungen")

    if args.min_count is not None and len(matches) < args.min_count:
        print(f"FEHLER: nur {len(matches)} Spiele, erwartet mindestens {args.min_count}")
        return 1
    if errs:
        return 1
    if args.strict and warns:
        print("FEHLER: --strict und es gibt Warnungen")
        return 1
    print("OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
