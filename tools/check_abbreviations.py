#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Findet abgekuerzte Torschuetzennamen im Seed.

OpenLigaDB kuerzt Vornamen uneinheitlich ab ("C. Uzun" statt "Can Uzun").
providers.loese_abkuerzung() faengt den Regelfall ab, laesst aber bewusst
alles stehen, was es nicht eindeutig aufloesen kann - lieber abgekuerzt und
richtig als ausgeschrieben und geraten.

Dieses Skript macht die Reste sichtbar. Jeder Treffer gehoert entweder in
providers._SCORER_ALIAS (Schluessel: goalGetterID aus der API) oder von Hand
in den Seed.

    python3 check_abbreviations.py ../data/seed_matches.json

Rueckgabewert 1, wenn Abkuerzungen gefunden wurden - so laesst sich das
Skript auch als Schranke in der Action verwenden.
"""
import json
import re
import sys
from collections import defaultdict

ABK_RE = re.compile(r"^([A-ZÄÖÜ])\.\s*(\S.*)$")


def main():
    pfad = sys.argv[1] if len(sys.argv) > 1 else "data/seed_matches.json"
    matches = json.load(open(pfad, encoding="utf-8"))["matches"]

    # name -> {gender: [spiel-ids]}
    treffer = defaultdict(lambda: defaultdict(list))
    voll = defaultdict(set)          # gender -> ausgeschriebene Namen

    for m in matches:
        g = m.get("gender") or "?"
        for tor in m.get("goals") or []:
            name = (tor.get("scorer") or "").strip()
            if not name:
                continue
            if ABK_RE.match(name):
                treffer[name][g].append(m["id"])
            elif " " in name:
                voll[g].add(name)

    if not treffer:
        print(f"{pfad}: keine abgekuerzten Torschuetzennamen.")
        return 0

    print(f"{pfad}: {len(treffer)} abgekuerzte(r) Name(n)\n")
    for name in sorted(treffer):
        for gender, ids in sorted(treffer[name].items()):
            initial, nachname = ABK_RE.match(name).groups()
            kandidaten = sorted(k for k in voll[gender]
                                if k.startswith(initial) and k.endswith(" " + nachname.strip()))
            print(f"  {name}  ({gender}, {len(ids)}x)")
            print(f"    Spiele     : {', '.join(ids[:4])}"
                  + (" …" if len(ids) > 4 else ""))
            if kandidaten:
                print(f"    Kandidaten : {', '.join(kandidaten)}"
                      + ("   <- mehrdeutig, Alias noetig" if len(kandidaten) > 1 else ""))
            else:
                print("    Kandidaten : keiner im Bestand – Alias oder Handeintrag noetig")
    return 1


if __name__ == "__main__":
    sys.exit(main())
