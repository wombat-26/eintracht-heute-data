#!/usr/bin/env python3
"""Entscheidet, ob ein Lauf waehrend eines laufenden Spiels noetig ist.

Die Action kann nicht wissen, wann Eintracht spielt - Cron kennt nur die
Uhr. Deshalb decken die Wochenend-Crons grosszuegige Fenster ab, und dieses
Skript entscheidet zur Laufzeit, ob dahinter Arbeit steckt. Ein Lauf ohne
Spiel endet damit nach wenigen Sekunden, statt die Quellen sinnlos
abzufragen.

Ausgabe nach GITHUB_OUTPUT: run=true|false und grund=<Text>.

Aufruf ohne Umgebung (lokal) schreibt die Entscheidung nach stdout:

    python3 live_gate.py --seed ../data/seed_matches.json
    python3 live_gate.py --seed ../data/seed_matches.json --jetzt 2026-09-19T20:00
"""
import argparse, json, os, sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")

# Ab wann nach Anpfiff der erste Lauf sinnvoll ist. Vorher steht ohnehin
# nichts Neues in den Quellen.
VORLAUF_MIN = 15
# Bis wann nach Anpfiff weitergelaufen wird: 90 Minuten Spielzeit, Halbzeit,
# Nachspielzeit und eine Reserve, damit der Abpfiff sicher erfasst wird.
NACHLAUF_MIN = 165
# Spiele ohne bekannte Anstosszeit stehen im Seed auf 00:00. Fuer die faellt
# das Fenster auf den ganzen Nachmittag und Abend zurueck, statt sie
# auszulassen - ein unnoetiger Lauf ist billiger als ein verpasstes Ergebnis.
UNBEKANNT_VON, UNBEKANNT_BIS = 12, 23

# Die taeglichen Grundlaeufe laufen immer, unabhaengig davon, ob gespielt
# wird. Sie holen Ansetzungen, Torschuetzinnen und alles, was nach dem
# Schlusspfiff noch nachgetragen wird.
BASIS_CRONS = {
    "20 5,19,21 * * *",
    # Der Lauf am 1. und 15. sucht fehlende Quellenlinks auf
    # eintracht-archiv.de. Er haengt nicht an einem Spiel und muss deshalb
    # unabhaengig vom Livefenster durchlaufen.
    "40 4 1,15 * *",
}


def laufende_spiele(matches, jetzt):
    """Spiele, deren Livefenster den Zeitpunkt einschliesst."""
    treffer = []
    for m in matches:
        roh = (m.get("date") or "")[:19]
        if len(roh) < 19:
            continue
        try:
            anstoss = datetime.fromisoformat(roh).replace(tzinfo=BERLIN)
        except ValueError:
            continue
        if anstoss.date() != jetzt.date():
            continue
        if anstoss.hour == 0 and anstoss.minute == 0:
            von = anstoss.replace(hour=UNBEKANNT_VON)
            bis = anstoss.replace(hour=UNBEKANNT_BIS)
        else:
            von = anstoss + timedelta(minutes=VORLAUF_MIN)
            bis = anstoss + timedelta(minutes=NACHLAUF_MIN)
        if von <= jetzt <= bis:
            treffer.append(m)
    return treffer


def entscheide(matches, jetzt, event, cron):
    """-> (run, grund). Alles ausser den Livefenster-Crons laeuft immer."""
    if event != "schedule":
        return True, f"Ereignis {event or 'unbekannt'} – kein Livefenster-Lauf."
    if cron in BASIS_CRONS:
        return True, f"Grundlauf ({cron})."
    spiele = laufende_spiele(matches, jetzt)
    if not spiele:
        return False, (f"Kein laufendes Spiel um "
                       f"{jetzt.strftime('%d.%m.%Y %H:%M')} Uhr.")
    namen = ", ".join(f"{m.get('homeTeam')} – {m.get('awayTeam')} "
                      f"({'Frauen' if m.get('gender') == 'women' else 'Männer'}, "
                      f"Anstoß {m['date'][11:16]})" for m in spiele)
    return True, f"Laufendes Spiel: {namen}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seed", default="../data/seed_matches.json")
    p.add_argument("--jetzt", help="ISO-Zeitpunkt statt der Uhr, zum Testen")
    args = p.parse_args()

    jetzt = (datetime.fromisoformat(args.jetzt).replace(tzinfo=BERLIN)
             if args.jetzt else datetime.now(BERLIN))
    with open(args.seed, encoding="utf-8") as f:
        matches = json.load(f)["matches"]

    run, grund = entscheide(matches, jetzt,
                            os.environ.get("GITHUB_EVENT_NAME"),
                            os.environ.get("GATE_CRON"))

    ziel = os.environ.get("GITHUB_OUTPUT")
    if ziel:
        with open(ziel, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")
            f.write(f"grund={grund}\n")
    print(f"run={run}: {grund}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
