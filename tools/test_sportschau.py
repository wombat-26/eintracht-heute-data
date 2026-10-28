#!/usr/bin/env python3
"""Offline-Test des Sportschau-Abgleichs gegen gespeicherte Ausschnitte.

Lauf: python3 test_sportschau.py   (aus tools/)

Die Fixtures in tests/fixtures/sportschau_*.html sind Ausschnitte der
Spielplan-Uebersichten von sportschau.de. Schlaegt der Test fehl, nachdem er
einmal lief, hat entweder der Parser oder das Sportschau-Layout sich
geaendert; im zweiten Fall gehoeren neue Ausschnitte her.
"""
import os, sys, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import providers
from seedkit import make_id

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


def als_openligadb(f, **aenderung):
    m = {"id": make_id(f["date"], f["homeTeam"], f["awayTeam"]),
         "date": f["date"], "matchday": f["matchday"],
         "homeTeam": f["homeTeam"], "awayTeam": f["awayTeam"],
         "homeScore": f["homeScore"], "awayScore": f["awayScore"]}
    m.update(aenderung)
    return m


def main():
    fehler = 0

    print("Spieltag lesen")
    tag, maenner = providers.sportschau_spieltag(
        lies("sportschau_bundesliga_spieltag7.html"), 2026)
    fehler += pruefe(tag == 7 and len(maenner) == 4, f"Spieltag 7, 4 Spiele ({tag}, {len(maenner)})")
    erstes = maenner[0]
    fehler += pruefe(erstes["homeTeam"] == "VfB Stuttgart"
                     and erstes["awayTeam"] == "Borussia Mönchengladbach",
                     f"Paarung {erstes['homeTeam']} – {erstes['awayTeam']}")
    fehler += pruefe((erstes["homeScore"], erstes["awayScore"]) == (2, 3),
                     f"Endstand, nicht Halbzeit ({erstes['homeScore']}:{erstes['awayScore']})")
    fehler += pruefe(erstes["date"] == "2026-10-23T20:30:00",
                     f"Sommerzeit: 18:30 UTC = 20:30 ({erstes['date']})")
    fehler += pruefe(all(f["homeScore"] is not None for f in maenner), "beendete Spiele haben ein Ergebnis")

    tag, frauen = providers.sportschau_spieltag(
        lies("sportschau_bundesliga_frauen_spieltag9.html"), 2026)
    fehler += pruefe(tag == 9 and len(frauen) == 3, f"Frauen: Spieltag 9, 3 Spiele ({tag}, {len(frauen)})")
    fehler += pruefe(frauen[0]["date"] == "2026-11-06T18:30:00",
                     f"Winterzeit: 17:30 UTC = 18:30 ({frauen[0]['date']})")
    fehler += pruefe(all(f["homeScore"] is None and f["awayScore"] is None for f in frauen),
                     "Spiele vor dem Anstoss haben kein Ergebnis (nicht 0:0)")
    fehler += pruefe(providers.sportschau_spieltag(
        lies("sportschau_bundesliga_spieltag7.html"), 2025) == (None, []),
        "Seite einer anderen Saison wird verworfen")
    fehler += pruefe(providers.sportschau_spieltag("<html><title>Startseite</title></html>", 2026)
                     == (None, []), "Seite ohne Spieltag wird verworfen")

    print("Abgleich")
    sauber = [als_openligadb(f) for f in maenner]
    fehler += pruefe(providers.abweichungen(sauber, maenner) == [], "identische Daten: keine Meldung")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], date="2026-11-06T20:30:00")], frauen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin"
                     and abw[0][3] == "2026-11-06 18:30", f"falsche Anstosszeit gemeldet ({abw})")

    abw = providers.abweichungen(
        [als_openligadb(maenner[0], homeScore=3)], maenner)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Ergebnis" and abw[0][3] == "2:3",
                     f"falsches Ergebnis gemeldet ({abw})")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], awayTeam="Phantom FC")], frauen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Paarung",
                     f"unbekannte Paarung gemeldet ({abw})")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], homeScore=1, awayScore=0)], frauen)
    fehler += pruefe(abw == [], "Zwischenstand bei OpenLigaDB ist keine Abweichung")

    abw = providers.abweichungen(
        [als_openligadb(frauen[0], matchday=12)], frauen)
    fehler += pruefe(abw == [], "anderer Spieltag als die Seite fuehrt: nichts zu pruefen")

    # Ohne Uhrzeit bei Sportschau ("--:--") zaehlt nur das Datum.
    ohne = lies("sportschau_bundesliga_frauen_spieltag9.html").replace(
        'class="match-time">18:30<', 'class="match-time">--:--<', 1)
    _, offen = providers.sportschau_spieltag(ohne, 2026)
    fehler += pruefe(offen[0]["date"] == "2026-11-06T00:00:00", f"Anstoss offen ({offen[0]['date']})")
    fehler += pruefe(providers.abweichungen(
        [als_openligadb(frauen[0], date="2026-11-06T18:30:00")], offen) == [],
        "fehlende Uhrzeit ist keine Abweichung")
    abw = providers.abweichungen(
        [als_openligadb(frauen[0], date="2026-11-07T18:30:00")], offen)
    fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin", "anderer Tag wird trotzdem gemeldet")

    print("sportschau_abgleich")
    original = providers.sportschau_seite
    meldungen = []
    log = meldungen.append
    try:
        providers.sportschau_seite = lambda c, g: lies("sportschau_bundesliga_frauen_spieltag9.html")
        abw = providers.sportschau_abgleich(
            2026, [als_openligadb(frauen[0], date="2026-11-06T20:30:00")],
            "bundesliga", "women", log=log)
        fehler += pruefe(len(abw) == 1 and abw[0][1] == "Termin", "Abweichung aus der Seite")

        providers.sportschau_seite = lambda c, g: (_ for _ in ()).throw(
            urllib.error.URLError("nicht erreichbar"))
        meldungen.clear()
        abw = providers.sportschau_abgleich(
            2026, [als_openligadb(frauen[0])], "bundesliga", "women", log=log)
        fehler += pruefe(abw == [] and meldungen and "nicht erreichbar" in meldungen[0],
                         "Ausfall ist ein Hinweis, kein Fehler")

        providers.sportschau_seite = lambda c, g: "<html><title>Wartung</title></html>"
        meldungen.clear()
        abw = providers.sportschau_abgleich(
            2026, [als_openligadb(frauen[0])], "bundesliga", "women", log=log)
        fehler += pruefe(abw == [] and meldungen and "Layout" in meldungen[0],
                         "Seite ohne Spiele ist ein Hinweis")

        def nie(c, g):
            raise AssertionError("darf nicht abgerufen werden")
        providers.sportschau_seite = nie
        fehler += pruefe(providers.sportschau_abgleich(2026, [als_openligadb(frauen[0])],
                                                       "dfbPokal", "women") == [],
                         "unbekannter Wettbewerb: keine Anfrage")
        fehler += pruefe(providers.sportschau_abgleich(2026, [], "bundesliga", "women") == [],
                         "keine Spiele: keine Anfrage")
    finally:
        providers.sportschau_seite = original

    print()
    if fehler:
        print(f"{fehler} Pruefung(en) fehlgeschlagen.")
        return 1
    print("Alle Pruefungen bestanden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
