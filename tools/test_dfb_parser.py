#!/usr/bin/env python3
"""Offline-Test des DFB-Spielplan-Parsers (Pokal der Frauen) gegen echtes,
gespeichertes HTML.

Lauf: python3 test_dfb_parser.py   (aus tools/)

Bewusst ohne Netz und ohne Testframework - die Fixtures in tests/fixtures
sind unveraenderte Abzuege von datencenter.dfb.de. Schlaegt der Test fehl,
nachdem er einmal lief, hat entweder der Parser oder das DFB-Layout sich
geaendert; im zweiten Fall gehoeren neue Abzuege her.
"""
import os, sys

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


def main():
    fehler = 0

    # --- Spielplan: eine Anfrage, ganze Saison ---
    print("Vereinsspielplan 2026/27")
    fixtures = providers.dfb_fixtures(lies("dfb_spielplan_2026.html"))
    fehler += pruefe(len(fixtures) == 26, f"26 Spiele erkannt (gefunden: {len(fixtures)})")

    erste = next((f for f in fixtures if f["dfbId"] == "2426960"), None)
    fehler += pruefe(erste is not None, "Spiel 2426960 im Spielplan")
    if erste:
        fehler += pruefe(erste["date"] == "2026-08-22T12:00:00", f"Datum {erste['date']}")
        fehler += pruefe(erste["homeTeam"] == "Eintracht Frankfurt", f"Heim {erste['homeTeam']}")
        fehler += pruefe(erste["awayTeam"] == "1. FC Köln", f"Gast {erste['awayTeam']}")
        fehler += pruefe((erste["homeScore"], erste["awayScore"]) == (2, 0),
                         f"Ergebnis {erste['homeScore']}:{erste['awayScore']}")
        fehler += pruefe(erste["matchday"] == 1, f"Spieltag {erste['matchday']}")
        # Die Anstosszeit steht als "12:00 Uhr" ueber dem Ergebnis - sie darf
        # nicht als Spielstand durchgehen.
        fehler += pruefe(erste["kickoffText"] == "12:00", "Anstosszeit nicht mit Ergebnis verwechselt")

    ungespielt = [f for f in fixtures if f["homeScore"] is None]
    fehler += pruefe(len(ungespielt) > 0, f"{len(ungespielt)} Spiele ohne Ergebnis erkannt")

    # Noch nicht terminierte Spieltage zeigt der DFB als Zeitraum.
    zr = next((f for f in fixtures if f.get("zeitraum")), None)
    fehler += pruefe(zr is not None, "Zeitraum im Spielplan erkannt"
                     + (f" ({zr['zeitraum'][0]} bis {zr['zeitraum'][1]})" if zr else ""))

    # --- Die Saison-Slugs aus dem Auswahlfeld, Grundlage der Slug-Suche ---
    print("Saison-Erkennung")
    fehler += pruefe(providers._dfb_saison_passt("google-pixel-frauen-bundesliga-2026-2027", 2026),
                     "aktueller Slug-Aufbau")
    fehler += pruefe(providers._dfb_saison_passt("2022-23", 2022), "alter Slug-Aufbau")
    fehler += pruefe(not providers._dfb_saison_passt("2022-23", 2026), "falsche Saison wird verworfen")

    print()
    if fehler:
        print(f"{fehler} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
