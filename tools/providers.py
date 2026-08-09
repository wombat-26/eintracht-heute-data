#!/usr/bin/env python3
"""Datenquellen: OpenLigaDB (saisonweise) und ESPN (tagesweise).

Bildet OpenLigaDBProvider.swift und ESPNProvider.swift 1:1 nach - inklusive
Teamnamen-Normalisierung, ID-Erzeugung und der Auswahl des Endergebnisses.
Weicht das Python hier ab, driften Server- und Geraetestand auseinander.
"""
import json, urllib.request, urllib.error
from datetime import datetime, timedelta
from seedkit import slug, make_id, utc_to_berlin_str

OLDB_BASE = "https://api.openligadb.de"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
TEAM_NEEDLE = "eintracht frankfurt"
UA = {"User-Agent": "EintrachtHeute-SeedBot/1.0 (+github.com/wombat-26)"}


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------- OpenLigaDB ----------

# Identisch zu OpenLigaDBProvider.normalizeTeam
_TEAM_MAP = {
    "SV Werder Bremen": "Werder Bremen",
    "Bayern München": "FC Bayern München",
    "Bor. Mönchengladbach": "Borussia Mönchengladbach",
    "Bor. Dortmund": "Borussia Dortmund",
    "TSG 1899 Hoffenheim": "TSG Hoffenheim",
    "TSG Hoffenheim": "TSG Hoffenheim",
    "VfL Bochum 1848": "VfL Bochum",
    "1. FC Heidenheim 1846": "1. FC Heidenheim",
    "FC Schalke 04": "Schalke 04",
    # OpenLigaDB fuehrt Bielefeld als "DSC Arminia Bielefeld", der Seed als
    # "Arminia Bielefeld" (35 Spiele). Ohne die Zuordnung erzeugt der
    # Slug-Generator "dscarmin" statt "arminiab" - jedes Bielefeld-Spiel
    # laege danach doppelt vor.
    "DSC Arminia Bielefeld": "Arminia Bielefeld",
}


def normalize_team(name):
    basis = name[:-len(" Frauen")] if name.endswith(" Frauen") else name
    return _TEAM_MAP.get(basis, basis)


# Identisch zu Competition.from(leagueName:)
def competition_from(league_name):
    n = (league_name or "").lower()
    if "2. bundesliga" in n or "2.bundesliga" in n or "zweite bundesliga" in n:
        return "bundesliga2"
    if "bundesliga" in n:                       return "bundesliga"
    if "oberliga" in n:                         return "oberliga"
    if ("deutsche meisterschaft" in n or "meisterschaftsendrunde" in n
            or "endrunde" in n):                return "meisterschaft"
    if "ligapokal" in n or "liga-pokal" in n or "liga pokal" in n:
        return "ligaPokal"
    if "supercup" in n or "super cup" in n or "dfl-supercup" in n:
        return "supercup"
    if "pokal" in n:                            return "dfbPokal"
    if "champions" in n:                        return "championsLeague"
    if any(k in n for k in ("landesmeister", "european cup", "europa",
                            "uefa", "conference", "messe")):
        return "europacup"
    if "freund" in n or "friendly" in n or "test" in n:
        return "friendly"
    return "other"


def _normalisiere_name(roh):
    """"Koch, Robin" -> "Robin Koch".

    Der Seed nutzt durchgaengig "Vorname Nachname" - keiner der 2881 Namen
    enthaelt ein Komma. OpenLigaDB liefert teils die umgekehrte Form; wo der
    Sync ergaenzen darf, soll das Ergebnis nicht aus dem Rahmen fallen.
    Nur der eindeutige Fall wird gedreht: genau ein Komma, beide Teile
    nicht leer.
    """
    if not roh or not roh.strip():
        return "–"
    teile = roh.split(",")
    if len(teile) != 2:
        return roh.strip()
    nach, vor = teile[0].strip(), teile[1].strip()
    return f"{vor} {nach}" if nach and vor else roh.strip()


def _parse_goals(goals):
    """OpenLigaDB liefert je Tor den neuen Spielstand - daraus ableiten,
    fuer welches Team es fiel (welcher Wert sich erhoeht hat)."""
    if not goals:
        return []
    out, prev1, prev2 = [], 0, 0
    for g in sorted(goals, key=lambda x: (x.get("matchMinute") or 0)):
        s1 = g.get("scoreTeam1") if g.get("scoreTeam1") is not None else prev1
        s2 = g.get("scoreTeam2") if g.get("scoreTeam2") is not None else prev2
        for_home = s1 > prev1
        prev1, prev2 = s1, s2
        out.append({
            "minute": g.get("matchMinute"),
            "scorer": _normalisiere_name(g.get("goalGetterName")),
            "forHome": bool(for_home),
            "isPenalty": bool(g.get("isPenalty")),
            "isOwnGoal": bool(g.get("isOwnGoal")),
            "order": len(out),
        })
    return out


def openligadb(league, season, gender, fallback_competition=None):
    """Alle Eintracht-Spiele einer Liga-Saison als Seed-Dicts."""
    raw = _get(f"{OLDB_BASE}/getmatchdata/{league}/{season}")
    label = f"{season}/{str(season + 1)[-2:]}"
    out = []
    for m in raw:
        t1 = ((m.get("team1") or {}).get("teamName") or "")
        t2 = ((m.get("team2") or {}).get("teamName") or "")
        # Die Zweitvertretung ("Eintracht Frankfurt II") gehoert nicht in
        # die Bilanz der ersten Mannschaft.
        if " II" in t1 or " II" in t2:
            continue
        if TEAM_NEEDLE not in t1.lower() and TEAM_NEEDLE not in t2.lower():
            continue

        home, away = normalize_team(t1), normalize_team(t2)
        res = m.get("matchResults") or []
        end = (next((r for r in res if r.get("resultTypeID") == 2), None)
               or next((r for r in res if "endergebnis" in (r.get("resultName") or "").lower()), None)
               or next((r for r in res if r.get("resultTypeID") == 0), None)
               or (max(res, key=lambda r: r.get("resultOrderID") or 0) if res else None))
        half = (next((r for r in res if r.get("resultTypeID") == 1), None)
                or next((r for r in res if "halbzeit" in (r.get("resultName") or "").lower()), None))

        dt = (m.get("matchDateTime") or "")[:19]
        if len(dt) == 16:
            dt += ":00"
        comp = competition_from(m.get("leagueName"))
        if comp == "other":
            comp = fallback_competition or "bundesliga"

        out.append({
            "id": make_id(dt, home, away),
            "date": dt,
            "kickoffText": dt[11:16] if len(dt) >= 16 else None,
            "competition": comp,
            "season": label,
            "matchday": (m.get("group") or {}).get("groupOrderID"),
            "homeTeam": home, "awayTeam": away,
            "homeScore": end.get("pointsTeam1") if end else None,
            "awayScore": end.get("pointsTeam2") if end else None,
            "halftimeHome": half.get("pointsTeam1") if half else None,
            "halftimeAway": half.get("pointsTeam2") if half else None,
            "isFinished": bool(m.get("matchIsFinished")),
            "goalsLoaded": False,
            "note": None, "sourceUrl": None,
            "goals": _parse_goals(m.get("goals")),
            "gender": gender,
        })
    return out


# ---------- ESPN ----------

def espn(slug_name, tag, gender, competition="championsLeague"):
    """Alle Eintracht-Spiele eines Kalendertags. `tag` ist ein date-Objekt;
    ESPN bucketet serverseitig nach UTC-Tagen."""
    url = f"{ESPN_BASE}/{slug_name}/scoreboard?dates={tag.strftime('%Y%m%d')}"
    try:
        payload = _get(url)
    except (urllib.error.HTTPError, urllib.error.URLError):
        return []

    out = []
    for ev in payload.get("events") or []:
        comps = ev.get("competitions") or []
        if not comps:
            continue
        c = comps[0]
        teams = c.get("competitors") or []
        home = next((t for t in teams if t.get("homeAway") == "home"), None)
        away = next((t for t in teams if t.get("homeAway") == "away"), None)
        if not home or not away:
            continue
        hn = (home.get("team") or {}).get("displayName") or ""
        an = (away.get("team") or {}).get("displayName") or ""
        if TEAM_NEEDLE not in hn.lower() and TEAM_NEEDLE not in an.lower():
            continue

        # ESPN liefert "0" fuer beide Teams auch VOR Anpfiff, nicht null.
        state = ((c.get("status") or {}).get("type") or {}).get("state")
        live_or_done = state != "pre"
        def score(t):
            try:
                return int(t.get("score")) if live_or_done else None
            except (TypeError, ValueError):
                return None

        datum = utc_to_berlin_str(ev["date"])
        out.append({
            "id": make_id(datum, hn, an),
            "date": datum,
            "kickoffText": datum[11:16],
            "competition": competition,
            "season": _season_label(datum),
            "matchday": None,
            "homeTeam": hn, "awayTeam": an,
            "homeScore": score(home), "awayScore": score(away),
            "halftimeHome": None, "halftimeAway": None,
            "isFinished": bool(((c.get("status") or {}).get("type") or {}).get("completed")),
            "goalsLoaded": False,
            "note": None, "sourceUrl": None,
            "goals": _espn_goals(c.get("details") or [], (home.get("team") or {}).get("id")),
            "gender": gender,
        })
    return out


def _season_label(datum_iso):
    """Wie ESPNProvider.seasonLabel - Schnitt im Juli, weil die
    UWCL-Qualifikation schon Ende Juli beginnen kann."""
    jahr, monat = int(datum_iso[:4]), int(datum_iso[5:7])
    start = jahr if monat >= 7 else jahr - 1
    return f"{start}/{str(start + 1)[-2:]}"


def _espn_goals(details, home_team_id):
    out = []
    for d in details:
        if not d.get("scoringPlay"):
            continue
        ath = d.get("athletesInvolved") or []
        if not ath:
            continue
        clock = ((d.get("clock") or {}).get("displayValue") or "")
        ziffern = ""
        for ch in clock:
            if ch.isdigit():
                ziffern += ch
            else:
                break
        out.append({
            "minute": int(ziffern) if ziffern else None,
            "scorer": ath[0].get("displayName") or "–",
            "forHome": (d.get("team") or {}).get("id") == home_team_id,
            "isPenalty": bool(d.get("penaltyKick")),
            "isOwnGoal": bool(d.get("ownGoal")),
            "order": len(out),
        })
    return out
