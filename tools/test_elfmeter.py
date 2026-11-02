#!/usr/bin/env python3
"""Offline-Test: Verlaengerung und Elfmeterschiessen (OpenLigaDB und DFB).

Lauf: python3 test_elfmeter.py   (aus tools/)

Anlass: DFB-Pokal der Frauen, 1. FC Nuernberg - Eintracht Frankfurt am
01.11.2026, 0:0 nach Verlaengerung, 2:4 im Elfmeterschiessen. Das
DFB-Datencenter fuehrt die Partie im Spielplan als "2 : 4 i.E.", OpenLigaDB
erfasste die Elfmeter als sechs Tore ohne Namen mit "Endergebnis" 2:4. Im
Seed und in der App stand dadurch 2:4 samt sechs namenloser Torschuetzinnen.

Die Fixtures sind unveraenderte Abzuege der echten Antworten.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests", "fixtures")


def lies(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def pruefe(bedingung, text):
    if not bedingung:
        print(f"FEHLER: {text}")
        return 1
    print(f"  ok: {text}")
    return 0


def oldb(name):
    m = json.loads(lies(name))
    return providers._verlaengerung_und_elfmeter(m.get("matchResults") or [], m.get("goals"))


def main():
    fehler = 0

    print("DFB-Spielplan: \"i.E.\" ist kein Ergebnis")
    fx = {f["dfbId"]: f for f in providers.dfb_fixtures(lies("dfb_pokal_frauen_spielplan_2026.html"))}
    n = fx.get("2431335")
    fehler += pruefe(n is not None, "Nuernberg - Eintracht im Spielplan")
    if n:
        fehler += pruefe(n["elfmeter"] == (2, 4), f"Elfmeterschiessen 2:4 erkannt ({n['elfmeter']})")
        fehler += pruefe(n["homeScore"] is None and n["awayScore"] is None,
                         "Elfmeterstand nicht als Ergebnis uebernommen")
    k = fx.get("2429027")
    if k:
        fehler += pruefe((k["homeScore"], k["awayScore"], k["elfmeter"]) == (1, 6, None),
                         "Karlsruhe 1:6 unveraendert, ohne Elfmeter")

    print("DFB-Detailseite: Endstand")
    stand = providers.dfb_endstand_elfmeter(lies("dfb_detail_nuernberg_eintracht_ie.html"))
    fehler += pruefe(stand == (0, 0, 0, 0), f"0:0 nach Verlaengerung, Halbzeit 0:0 ({stand})")
    fehler += pruefe(providers.dfb_endstand_elfmeter("<html></html>") is None,
                     "Seite ohne Staende -> None")

    print("OpenLigaDB: Frauen, Elfmeter als Tore erfasst")
    r = oldb("oldb_dfbf_88743_elfmeter.json")
    fehler += pruefe(r is not None and r[:3] == (0, 0, "n.E. 2:4"),
                     f"0:0, n.E. 2:4 ({r[:3] if r else None})")
    fehler += pruefe(r is not None and r[3] == [], "die sechs Elfmeter fallen aus der Torliste")

    print("OpenLigaDB: Frauen, Verlaengerung ohne Elfmeter")
    r = oldb("oldb_dfbf_88742_verlaengerung.json")
    fehler += pruefe(r is not None and r[:3] == (3, 4, None),
                     f"3:4 n.V. trotz \"Verlaengerung\" 3:3 ({r[:3] if r else None})")

    print("OpenLigaDB: Maenner, Elfmeter mit \"Nachspielzeit\"")
    r = oldb("oldb_dfb_79589_elfmeter.json")
    fehler += pruefe(r is not None and r[:3] == (1, 1, "n.E. 2:4"),
                     f"Eintracht - Dortmund 1:1, n.E. 2:4 ({r[:3] if r else None})")
    fehler += pruefe(r is not None and len(r[3]) == 2,
                     f"zwei echte Tore bleiben ({len(r[3]) if r else None})")

    print("OpenLigaDB: normales Spiel bleibt unberuehrt")
    fehler += pruefe(providers._verlaengerung_und_elfmeter(
        [{"resultTypeID": 2, "resultName": "Endergebnis", "pointsTeam1": 2, "pointsTeam2": 1}], []) is None,
        "ohne Verlaengerung/Elfmeter -> None")

    print()
    if fehler:
        print(f"{fehler} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
