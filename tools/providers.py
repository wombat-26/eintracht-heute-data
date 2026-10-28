#!/usr/bin/env python3
"""Datenquellen: OpenLigaDB (saisonweise), ESPN (tagesweise), das
DFB-Datencenter (Spielplan des Pokals der Frauen) und Sportschau (Abgleich
von Termin und Ergebnis, Torschuetzinnen der Frauen-Bundesliga).

Bildet OpenLigaDBProvider.swift und ESPNProvider.swift 1:1 nach - inklusive
Teamnamen-Normalisierung, ID-Erzeugung und der Auswahl des Endergebnisses.
Weicht das Python hier ab, driften Server- und Geraetestand auseinander.

Datencenter und Sportschau haben bewusst kein Swift-Gegenstueck: Es ist HTML,
kein JSON, und gehoert damit in die Pipeline, nicht aufs Geraet.
"""
import functools, json, re, time, urllib.request, urllib.error
import html as html_mod
from datetime import datetime, timedelta
from seedkit import slug, make_id, utc_to_berlin_str, zerlege_abkuerzung

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
    # Gleiches Muster bei den Sechzigern: OpenLigaDB schreibt
    # "TSV 1860 München", der Seed fuehrt sie als "1860 München" (46 Spiele).
    "TSV 1860 München": "1860 München",
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


# Explizite Zuordnung fuer abgekuerzte Vornamen, die die Automatik unten
# nicht aufloesen kann - etwa weil der Spieler im Bestand noch gar nicht
# vorkommt oder zwei Namensvettern denselben Anfangsbuchstaben haben.
#
# Schluessel ist die goalGetterID von OpenLigaDB, nicht der Namensstring:
# Die ID ist je Spieler stabil und eindeutig, waehrend derselbe Spieler mal
# als "C. Uzun", mal als "Uzun, C." und mal ausgeschrieben ankommt.
#
# Leer ist der Normalfall. Ein Eintrag ist nur noetig, wenn nach einem Lauf
# ein abgekuerzter Name im Seed steht (pruefbar mit check_abbreviations.py).
_SCORER_ALIAS = {
    # 20128: "Can Uzun",
}


def loese_abkuerzung(name, getter_id=None, roster=None):
    """Macht aus "C. Uzun" wieder "Can Uzun".

    OpenLigaDB kuerzt Vornamen uneinheitlich ab - derselbe Spieler steht mal
    voll, mal abgekuerzt da. Der Seed fuehrt durchgaengig ausgeschriebene
    Namen; ohne Aufloesung stuenden in der Spielersuche zwei Eintraege fuer
    denselben Mann nebeneinander. Genau das Problem, das bei "Boateng" schon
    einmal von Hand aufgeraeumt werden musste.

    Zwei Stufen, explizit vor automatisch:
      1. _SCORER_ALIAS ueber die goalGetterID - hat immer Vorrang.
      2. Eindeutiger Treffer im Bestand: Gibt es dort GENAU EINEN Namen, der
         auf denselben Nachnamen endet und mit demselben Buchstaben beginnt,
         wird er uebernommen.

    Bei mehreren Kandidaten bleibt die Abkuerzung stehen. Ein abgekuerzter,
    aber richtiger Name ist harmloser als ein geratener falscher - und faellt
    in der Pruefung auf, wo ein falsch aufgeloester stillschweigend
    durchginge.
    """
    if getter_id is not None and getter_id in _SCORER_ALIAS:
        return _SCORER_ALIAS[getter_id]

    if not roster:
        return name
    zerlegt = zerlege_abkuerzung(name)
    if not zerlegt:
        return name
    initial, nachname = zerlegt

    # Selbst abgekuerzte Eintraege scheiden als Ziel aus. Sonst ist ein
    # abgekuerzter Name sein eigener Treffer: "A. Amaimouni-Echghouyab"
    # beginnt mit "A" und endet auf " Amaimouni-Echghouyab", passt also auf
    # sich selbst. Steht er durch einen frueheren Lauf bereits im Bestand,
    # gibt es zwei Kandidaten - der Fall gilt als mehrdeutig und nichts wird
    # aufgeloest. Der Bestand vergiftet sich also selbst, und zwar dauerhaft,
    # weil der Upsert vorhandene Torschuetzen aus dem Netz nicht ersetzt.
    treffer = {r for r in roster
               if r.startswith(initial) and r.endswith(" " + nachname)
               and zerlege_abkuerzung(r) is None}
    return treffer.pop() if len(treffer) == 1 else name


def _torsumme(g):
    """Spielstand nach dem Treffer als eine Zahl - steigt bei echten Toren
    streng an."""
    return (g.get("scoreTeam1") or 0) + (g.get("scoreTeam2") or 0)


def _parse_goals(goals, roster=None):
    """OpenLigaDB liefert je Tor den neuen Spielstand - daraus ableiten,
    fuer welches Team es fiel (welcher Wert sich erhoeht hat).

    Sortiert wird nach Torsumme, die Minute entscheidet nur bei Gleichstand.
    Hat die Liste mehr Eintraege, als Tore gefallen sind, werden Eintraege
    verworfen, die den Stand nicht erhoehen (Phantomtore, Dubletten) -
    Anlass war ein nicht loeschbarer Rest "0:0, K. Buehl, 18." in Match
    85755 am 03.10.2026. Nur bei Ueberzahl, weil ein solcher Eintrag auch
    ein echtes Tor mit falsch erfasstem Stand sein kann (Match 77340,
    Svanberg als 0:0). Muss mit parseGoals in OpenLigaDBProvider.swift
    uebereinstimmen."""
    if not goals:
        return []
    out, prev1, prev2 = [], 0, 0
    zu_viele = len(goals) > max(_torsumme(g) for g in goals)
    for g in sorted(goals, key=lambda x: (_torsumme(x), x.get("matchMinute") or 0)):
        s1 = g.get("scoreTeam1") if g.get("scoreTeam1") is not None else prev1
        s2 = g.get("scoreTeam2") if g.get("scoreTeam2") is not None else prev2
        if zu_viele and s1 + s2 <= prev1 + prev2:
            continue
        for_home = s1 > prev1
        prev1, prev2 = s1, s2
        # Erst die Komma-Form drehen, dann die Abkuerzung aufloesen -
        # "Uzun, C." muss beide Schritte durchlaufen.
        name = _normalisiere_name(g.get("goalGetterName"))
        name = loese_abkuerzung(name, g.get("goalGetterID"), roster)
        out.append({
            "minute": g.get("matchMinute"),
            "scorer": name,
            "forHome": bool(for_home),
            "isPenalty": bool(g.get("isPenalty")),
            "isOwnGoal": bool(g.get("isOwnGoal")),
            "order": len(out),
        })
    return out


def openligadb(league, season, gender, fallback_competition=None, roster=None):
    """Alle Eintracht-Spiele einer Liga-Saison als Seed-Dicts.

    `roster` ist die Menge der bereits bekannten, ausgeschriebenen
    Torschuetzennamen - siehe loese_abkuerzung(). Ohne sie greift nur die
    explizite Alias-Tabelle.
    """
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
            "goals": _parse_goals(m.get("goals"), roster),
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


# ---------- DFB-Datencenter ----------
#
# Eine Aufgabe, nur auf der Vereinsspielplan-Seite (eine Anfrage je Saison,
# kein Abruf einzelner Spiele): dfb_spiele(), der DFB-Pokal der Frauen, den
# sonst keine Quelle fuehrt. Den Abgleich von Termin und Ergebnis der
# OpenLigaDB-Spiele macht seit 28.10.2026 Sportschau, siehe sportschau_abgleich().
#
# Torschuetzen holt die Pipeline hier NICHT. Bis 28.10.2026 las ein Parser die
# Detailseiten der Spiele aus; die Nutzungsbedingungen des Datencenters
# untersagen Datencrawling und Datenscraping. Abgekuerzte Vornamen loest
# jetzt wikidata_namen.py auf, ueber den Query Service von Wikidata (CC0);
# die Torschuetzinnen der Frauen-Bundesliga kommen von Sportschau.
#
# Zwei Einschraenkungen, die den Zuschnitt bestimmen:
#
#  1. Es ist HTML. Ein Redesign beim DFB legt den Parser still. Deshalb faellt
#     der Ausfall immer weich aus - ein fehlender Abgleich ist kein Grund,
#     den Lauf rot zu faerben (Regel 4 zielt auf kaputte Daten, nicht auf
#     fehlende Ergaenzungen).
#  2. Der Wettbewerbs-Slug traegt den Sponsornamen. Vor Google Pixel war es
#     Flyeralarm, und der naechste Vertrag aendert ihn wieder.

DFB_BASE = "https://datencenter.dfb.de"
DFB_HEADERS = {
    "User-Agent": UA["User-Agent"],
    "Accept": "text/html, application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}
# Ohne diesen Header rendert der Rails-Server den Spielplan nicht aus,
# sondern laedt ihn per Turbo-Frame nach - die Antwort waere leer.
DFB_TURBO = {"Turbo-Frame": "spielplan"}

def _dfb_html(url, extra=None, timeout=30):
    kopf = dict(DFB_HEADERS)
    kopf.update(extra or {})
    req = urllib.request.Request(url, headers=kopf)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _text(roh):
    """Tags raus, Entities aufloesen, Leerraum normalisieren."""
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", roh))).strip()


_DFB_SAISON_JAHRE = re.compile(r"(\d{4})-(\d{2,4})$")


def _dfb_saison_passt(saison_slug, saison):
    m = _DFB_SAISON_JAHRE.search(saison_slug)
    return bool(m) and int(m.group(1)) == saison


# Innerhalb eines Laufs nur einmal je Saison und Wettbewerb abrufen:
# Einmal je Saison und Wettbewerb genuegt. Der Cache lebt
# nur so lange wie der Prozess, ein Lauf sieht also immer den aktuellen
# Stand.
@functools.lru_cache(maxsize=16)
def dfb_spielplan_seite(saison, wettbewerbe, team):
    """Holt die Vereinsspielplan-Seite einer Saison. -> (html, url) oder (None, None).

    Der Saison-Slug wird nicht gebaut, sondern erraten und dann korrigiert:
    Der DFB fuehrt drei Formate nebeneinander - "bundesliga-2026-2027" bei
    den Frauen, "2026-2027" bei den Maennern und "2026-27" im Pokal.
    Schlaegt der erste Versuch fehl, liefert der DFB trotzdem eine Seite, und
    die enthaelt im Saison-Auswahlfeld die tatsaechlich gueltigen Slugs. Aus
    denen wird der passende nachgezogen. Das faengt auch Formatwechsel ab:
    Bei den Frauen hiessen sie bis 2022/23 schlicht "2022-23".
    """
    for w in wettbewerbe:
        offen = [f"{w}-{saison}-{saison + 1}",
                 f"{saison}-{saison + 1}",
                 f"{saison}-{str(saison + 1)[-2:]}"]
        gesehen = set()
        while offen:
            s = offen.pop(0)
            if s in gesehen:
                continue
            gesehen.add(s)
            url = f"{DFB_BASE}/competitions/{w}/seasons/{s}/teams/{team}"
            try:
                seite = _dfb_html(url, DFB_TURBO)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError):
                continue
            if 'id="match_' in seite:
                return seite, url
            muster = rf"/competitions/{re.escape(w)}/seasons/([a-z0-9\-]+)"
            for kandidat in re.findall(muster, seite):
                if kandidat not in gesehen and _dfb_saison_passt(kandidat, saison):
                    offen.append(kandidat)
    return None, None


_DFB_ZEILE = re.compile(r'id="match_(\d+)"(.*?)(?=id="match_\d+"|\Z)', re.S)
# Noch nicht terminierte Spieltage zeigt der DFB als Zeitraum:
# "11.12. ~ 14.12.2026". Der Anfang traegt kein Jahr, deshalb griff
# _DFB_DATUM bisher das Ende des Zeitraums.
_DFB_ZEITRAUM = re.compile(r"(\d{2})\.(\d{2})\.\s*~\s*(\d{2})\.(\d{2})\.(\d{4})")
_DFB_DATUM = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}):(\d{2}))?")
_DFB_LINK = re.compile(r'href="(https://datencenter\.dfb\.de/datencenter/[^"]+?-\d{5,7})"')
_DFB_SPIELTAG = re.compile(r"/(\d+)-spieltag/")
_DFB_ERGEBNIS = re.compile(r"(\d+)\s*:\s*(\d+)")
_A_TEXT = re.compile(r"<a[^>]*>(.*?)</a>", re.S)


def _dfb_team_name(zeile, seite):
    """Vereinsname aus der Heim- bzw. Auswaertsspalte einer Spielplanzeile."""
    i = zeile.find(f"c-MatchTable-team--{seite}")
    if i < 0:
        return None
    # Fenster statt Rest der Zeile: Findet die Spalte kein <a>, soll die
    # Suche nicht in die naechste Spalte rutschen und dort den Gegner holen.
    m = _A_TEXT.search(zeile[i:i + 1500])
    return _text(m.group(1)) if m else None


def dfb_fixtures(seiten_html):
    """Die Zeilen des Vereinsspielplans als Dicts.

    Eine Anfrage liefert die ganze Saison mit Datum, Anstosszeit, Ergebnis
    und dem Link zum Spielschema. Die Torliste steht erst auf dieser
    Detailseite - deshalb hier die Vorauswahl, welche Seiten es ueberhaupt
    zu holen lohnt.
    """
    out = []
    for dfb_id, zeile in _DFB_ZEILE.findall(seiten_html):
        d = _DFB_DATUM.search(zeile)
        if not d:
            continue
        zeitraum = None
        z = _DFB_ZEITRAUM.search(_text(zeile))
        if z:
            t1, m1, t2, m2, j2 = z.groups()
            # Ueber den Jahreswechsel ("30.12. ~ 02.01.2027") gehoert der
            # Anfang ins Vorjahr.
            j1 = int(j2) - 1 if int(m1) > int(m2) else int(j2)
            zeitraum = (f"{j1}-{m1}-{t1}", f"{j2}-{m2}-{t2}")
        tag, monat, jahr, std, minute = d.groups()
        std, minute = std or "00", minute or "00"
        link = _DFB_LINK.search(zeile)
        spieltag = _DFB_SPIELTAG.search(link.group(1)) if link else None

        # Nur im Ergebnisfeld nach "x : y" suchen. Die Zeile enthaelt weiter
        # oben die Anstosszeit ("12:00 Uhr"), ein ungebundener Treffer waere
        # also regelmaessig ein Uhrzeit-Fehlfund.
        heim = gast = None
        j = zeile.find("c-MatchTable-score")
        if j >= 0:
            e = _DFB_ERGEBNIS.search(zeile[j:j + 400])
            if e:
                heim, gast = int(e.group(1)), int(e.group(2))

        out.append({
            "dfbId": dfb_id,
            "date": f"{jahr}-{monat}-{tag}T{std}:{minute}:00",
            "kickoffText": f"{std}:{minute}",
            "homeTeam": normalize_team(_dfb_team_name(zeile, "home") or ""),
            "awayTeam": normalize_team(_dfb_team_name(zeile, "away") or ""),
            "homeScore": heim,
            "awayScore": gast,
            "matchday": int(spieltag.group(1)) if spieltag else None,
            "url": link.group(1) if link else None,
            # (von, bis) als ISO-Datum, solange der Spieltag nicht
            # terminiert ist; sonst None. "date" steht dann weiterhin auf dem
            # Ende des Zeitraums, damit sich fuer bestehende Aufrufer nichts
            # aendert.
            "zeitraum": zeitraum,
        })
    return out


# ---------- eintracht-archiv.de ----------
#
# Quellenlink fuer die Spielkarte ("Daten: eintracht-archiv.de").
#
# Hier ist nichts zu recherchieren: Die URL ergibt sich aus Datum und
# Geschlecht. Von den 3486 im Seed vorhandenen Links folgen 3471 exakt
# diesem Muster:
#
#   Maenner  https://www.eintracht-archiv.de/<saison>/<JJJJ-MM-TT>st.html
#   Frauen   https://www.eintracht-archiv.de/<saison>f/f_<JJJJ-MM-TT>st.html
#
# <saison> ist das Startjahr der Saison, also 2026 fuer alles ab Juli 2026.
# Der Wettbewerb spielt keine Rolle, Pokal- und Europapokalspiele liegen im
# selben Ordner.
#
# Die elf Abweichler sind alle aelter als 1980 und betreffen Partien direkt
# an der Saisongrenze, die im Ordner der Vorsaison liegen (etwa 1970-07-29
# unter /1969/). Deshalb wird beide Ordner probiert.
#
# Warum ueberhaupt pruefen statt die URL blind einzutragen: Die Seiten
# entstehen von Hand und mit Verzug. Am 16.09.2026 lagen die Spiele bis zum
# 30.08. vor, alles danach lieferte 404. Ein blind gesetzter Link zeigte
# also auf eine Fehlerseite.

ARCHIV_BASIS = "https://www.eintracht-archiv.de"


def archiv_urls(datum, gender):
    """Mögliche Archiv-URLs eines Spiels, wahrscheinlichste zuerst."""
    jahr, monat = int(datum[:4]), int(datum[5:7])
    tag = datum[:10]
    saisons = [jahr if monat >= 7 else jahr - 1]
    # Partien direkt an der Saisongrenze liegen vereinzelt im Ordner der
    # Vorsaison - zweite Moeglichkeit, aber nur fuer Juli und August.
    if monat in (7, 8):
        saisons.append(saisons[0] - 1)
    if gender == "women":
        return [f"{ARCHIV_BASIS}/{s}f/f_{tag}st.html" for s in saisons]
    return [f"{ARCHIV_BASIS}/{s}/{tag}st.html" for s in saisons]


def _archiv_seite_passt(seiten_html, datum, gegner):
    """Steht auf der Seite wirklich dieses Spiel?

    Geprueft wird das Datum in deutscher Schreibweise und der Gegner. Ohne
    diese Kontrolle wuerde jede Seite, die der Server mit 200 beantwortet,
    als Treffer durchgehen.

    Entities muessen dabei aufgeloest werden: Die Seiten schreiben Umlaute
    als &ouml; und Aehnliches, ein roher Textvergleich fand "Köln" und
    "Nürnberg" deshalb nicht.
    """
    klar = _text(seiten_html)
    tag, monat, jahr = datum[8:10], datum[5:7], datum[:4]
    if f"{tag}.{monat}.{jahr}" not in klar:
        return False
    # Laengstes Wort des Gegnernamens als Probe - "1. FC" und "SV" stehen
    # auf jeder zweiten Seite, "Nürnberg" oder "Tönis" nicht.
    wort = max(re.findall(r"[\wÄÖÜäöüß-]{4,}", gegner or ""), key=len, default="")
    return not wort or wort.lower() in klar.lower()


def archiv_links(kandidaten, log=None, max_pruefungen=25, pause=0.5):
    """Ergaenzt fehlende Quellenlinks. -> Liste angereicherter Spiel-Dicts.

    `kandidaten` ist {id: Spiel-Dict}. Wie beim Datencenter kommen Kopien
    dieser Dicts zurueck, veraendert ist nur sourceUrl - der Upsert setzt
    das Feld ohnehin nur, wenn es gefuellt ankommt.

    Nicht gefundene Spiele bleiben unangetastet und werden beim naechsten
    Lauf erneut versucht; genau dafuer laeuft die Pruefung wiederholt.
    """
    protokoll = log or (lambda s: None)
    out, geprueft = [], 0
    for mid, m in sorted(kandidaten.items(), key=lambda kv: kv[1].get("date", "")):
        if geprueft >= max_pruefungen:
            protokoll(f"  eintracht-archiv: Grenze von {max_pruefungen} Pruefungen "
                      f"erreicht, Rest beim naechsten Lauf.")
            break
        datum = (m.get("date") or "")[:10]
        if len(datum) != 10:
            continue
        gegner = (m.get("awayTeam") if TEAM_NEEDLE in (m.get("homeTeam") or "").lower()
                  else m.get("homeTeam"))
        gefunden = None
        for url in archiv_urls(datum, m.get("gender")):
            geprueft += 1
            try:
                seite = _dfb_html(url, timeout=25)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    protokoll(f"  Hinweis: eintracht-archiv {url} – HTTP {e.code}")
                continue
            except (urllib.error.URLError, OSError) as e:
                protokoll(f"  Hinweis: eintracht-archiv {url} – {e}")
                continue
            finally:
                time.sleep(pause)
            if _archiv_seite_passt(seite, datum, gegner):
                gefunden = url
                break
            protokoll(f"  PRUEFEN: {url} antwortet, zeigt aber nicht "
                      f"{datum} gegen {gegner} – nicht uebernommen.")
        if not gefunden:
            continue
        angereichert = dict(m)
        angereichert["sourceUrl"] = gefunden
        out.append(angereichert)
        protokoll(f"  Archivlink {mid}: {gefunden}")
    return out


# DFB-Pokal der Frauen: einzige Partie, die sonst gar nicht ankaeme.
#
# OpenLigaDB fuehrt den Wettbewerb nicht (832 Ligen durchgesehen, fuer 2026
# gibt es ffb1, ffb2, wsc und fna - keinen Frauenpokal), ESPN kennt keine
# deutsche Frauenliga, und eintracht-archiv.de traegt erst Tage spaeter nach.
# Ohne diese Quelle fehlte das Spiel bis zum naechsten Seed-Nachtrag von Hand
# komplett in der App.
#
# Deshalb ist das Datencenter hier ausnahmsweise Primaerquelle und legt
# Spiele an, statt nur Tore zu ergaenzen. Der Slug ist nicht zu erraten
# (dfb-pokal-1132), die Saison laeuft im Pokalformat "2026-27".
DFB_PRIMAERQUELLEN = {
    ("dfbPokal", "women"): (("dfb-pokal-1132",), "eintracht-frankfurt-31426"),
}


def dfb_spiele(saison, competition, gender, log=None):
    """Spielplan eines Wettbewerbs als vollstaendige Datensaetze.

    Diese Funktion legt Spiele an. Sie liest nur die Spielplanseite - eine
    Anfrage fuer die ganze Saison, mit Datum, Anstosszeit, Vereinen und
    Ergebnis. Die Torliste bleibt leer: Torschuetzen der Frauen kommen aus
    keiner automatischen Quelle, sondern von eintracht-archiv.de.

    Halbzeitstand gibt es hier nicht: Die Spielplanzeile fuehrt ihn nicht,
    und dafuer jede Detailseite zu holen, waere der Ausbeute nicht wert.
    """
    protokoll = log or (lambda s: None)
    quelle = DFB_PRIMAERQUELLEN.get((competition, gender))
    if not quelle:
        return []
    wettbewerbe, team = quelle
    seite, url = dfb_spielplan_seite(saison, wettbewerbe, team)
    if not seite:
        protokoll(f"  Hinweis: DFB-Datencenter – kein Spielplan fuer "
                  f"{competition}/{gender} {saison}. Slug in "
                  f"DFB_PRIMAERQUELLEN pruefen.")
        return []

    out = []
    for f in dfb_fixtures(seite):
        if not f["homeTeam"] or not f["awayTeam"]:
            continue
        out.append({
            "id": make_id(f["date"], f["homeTeam"], f["awayTeam"]),
            "date": f["date"],
            "kickoffText": f["kickoffText"],
            "competition": competition,
            "season": _season_label(f["date"]),
            # Das Datencenter zaehlt Runden ("1-hauptrunde"), keine
            # Spieltage - und der Seed fuehrt bei Pokalspielen ohnehin
            # matchday: null.
            "matchday": None,
            "homeTeam": f["homeTeam"], "awayTeam": f["awayTeam"],
            "homeScore": f["homeScore"], "awayScore": f["awayScore"],
            "halftimeHome": None, "halftimeAway": None,
            # Das Datencenter tickert nicht mit: Bis zum Abpfiff steht dort
            # "-:-". Ein vorhandenes Ergebnis heisst deshalb, dass die Partie
            # durch ist.
            "isFinished": f["homeScore"] is not None,
            "goalsLoaded": False,
            "note": None, "sourceUrl": None,
            "goals": [],
            "gender": gender,
        })
    protokoll(f"  DFB {competition}/{gender}/{saison}: {len(out)} Spiele ({url})")
    return out


# ---------- Abgleich OpenLigaDB gegen Sportschau ----------
#
# OpenLigaDB ist bei den Frauen unzuverlaessig: Torschuetzinnen fehlen ganz,
# ein ganzer Import kam mit vertauschtem Tag und Monat, und Ansetzungen
# hinken hinterher. Am 03.10.2026 stand Eintracht – FC Bayern München
# (6. Spieltag) dort noch auf Sonntag 15:30, gespielt wurde Samstag 17:55.
# Aufgefallen ist das erst, als die App den falschen Tag zeigte - und das
# Gate haette am Spieltag keinen Live-Lauf durchgelassen, weil es den Seed
# nach Spielen von heute fragt.
#
# Deshalb wird der aktuelle Spieltag jeder OpenLigaDB-Liga gegen sportschau.de
# geprueft. Die Uebersichtsseite einer Liga zeigt genau einen Spieltag, mit
# Anstoss (UTC), Mannschaften und - sobald "Beendet" dasteht - Endergebnis.
# Das kostet zwei Anfragen je Lauf (Maenner und Frauen).
#
#   Termin    Datum und Anstoss
#   Ergebnis  sobald das Spiel bei Sportschau beendet ist
#   Paarung   gibt es das Spiel an diesem Spieltag bei Sportschau ueberhaupt?
#
# Torschuetzinnen: eigener Weg, siehe unten (sportschau_torschuetzinnen).
#
# Bewusst nur Meldung, keine Korrektur: Die App fragt OpenLigaDB selbst ab.
# Ein abweichender Termin im Seed wuerde dort als verschobener Platzhalter
# wieder entfernt. Behoben werden muss es an der Quelle.
#
# Die robots.txt von sportschau.de erlaubt den Abruf (gesperrt ist nur
# /live-und-ergebnisse/widget/); KI-Trainings-Bots sperrt sie, was hier nicht
# zutrifft: Die Daten werden verglichen, nicht gesammelt oder zum Training
# verwendet. Der User-Agent nennt Projekt und GitHub-Konto.

SPORTSCHAU_BASE = "https://www.sportschau.de"
SPORTSCHAU_QUELLEN = {
    ("bundesliga", "men"):
        "/live-und-ergebnisse/fussball/deutschland-bundesliga/spiele-und-ergebnisse",
    ("bundesliga", "women"):
        "/live-und-ergebnisse/fussball/deutschland-bundesliga-frauen/spiele-und-ergebnisse",
}
SPORTSCHAU_HEADERS = {
    "User-Agent": UA["User-Agent"],
    "Accept": "text/html, application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}


def abweichungen(fetched, zeilen):
    """Vergleicht OpenLigaDB-Datensaetze mit dem Spielplan einer Zweitquelle.
    -> [(id, art, openligadb, zweitquelle), ...]

    Zugeordnet wird ueber Spieltag und Paarung, nicht ueber die ID - die
    enthaelt das Datum, und genau das ist womoeglich falsch.

    Steht bei einer Seite noch keine Uhrzeit (00:00), wird nur das Datum
    verglichen; sonst meldete jeder Spieltag, den die eine Quelle schon
    terminiert hat und die andere noch nicht, eine Abweichung, die keine ist.

    Ein Ergebnis wird nur verglichen, wenn beide eines fuehren. Waehrend
    eines Spiels zeigt OpenLigaDB den Zwischenstand, die Zweitquelle noch
    nichts - das ist keine Abweichung.
    """
    def paarung(datum, heim, gast):
        return make_id(datum, heim, gast).split("-", 1)[1]

    ref, spieltage = {}, set()
    for f in zeilen:
        if f.get("matchday") is None or not f.get("homeTeam") or not f.get("awayTeam"):
            continue
        ref[(f["matchday"], paarung(f["date"], f["homeTeam"], f["awayTeam"]))] = f
        spieltage.add(f["matchday"])

    out = []
    for m in fetched:
        tag = m.get("matchday")
        f = ref.get((tag, paarung(m["date"], m["homeTeam"], m["awayTeam"])))
        if f is None:
            # Nur melden, wenn die Zweitquelle diesen Spieltag ueberhaupt
            # fuehrt - sonst waere es eine Luecke dort, nicht bei OpenLigaDB.
            if tag in spieltage:
                out.append((m["id"], "Paarung",
                            f"{tag}. Spieltag {m['homeTeam']} – {m['awayTeam']}",
                            "an diesem Spieltag nicht vorhanden"))
            continue

        ist, soll = m["date"][:16], f["date"][:16]
        ohne_uhrzeit = soll.endswith("T00:00") or ist.endswith("T00:00")
        if (soll[:10] != ist[:10]) if ohne_uhrzeit else (soll != ist):
            out.append((m["id"], "Termin", ist.replace("T", " "), soll.replace("T", " ")))

        if (m.get("homeScore") is not None and f.get("homeScore") is not None
                and (m["homeScore"], m.get("awayScore")) != (f["homeScore"], f["awayScore"])):
            out.append((m["id"], "Ergebnis",
                        f"{m['homeScore']}:{m['awayScore']}",
                        f"{f['homeScore']}:{f['awayScore']}"))
    return out


# Titel der Uebersichtsseite: "... Ergebnisse - 8. Spieltag - 2026/2027 | ..."
_SS_TITEL = re.compile(r"(\d+)\.\s*Spieltag\s*-\s*(\d{4})/(\d{4})")
_SS_SPIEL = re.compile(r'<li data-match_id="\d+".*?</li>', re.S)
_SS_ZEIT = re.compile(r'class="match-time">\s*(\d{1,2}:\d{2})\s*<')
_SS_ERGEBNIS = r'class="match-result match-result-{}">\s*<div class="match-result match-result-0">(\d+)<'


def sportschau_spieltag(seite, saison):
    """Liest die Uebersichtsseite einer Liga. -> (spieltag, [Spielzeile])

    Die Zeilen haben dieselbe Form wie dfb_fixtures(); abweichungen() nimmt
    beide. Ist die Seite nicht von dieser Saison oder fehlt der Spieltag im
    Titel, kommt (None, []) zurueck - lieber kein Abgleich als ein falscher.
    """
    titel = re.search(r"<title>(.*?)</title>", seite, re.S)
    m = _SS_TITEL.search(titel.group(1)) if titel else None
    if not m or int(m.group(2)) != saison:
        return None, []
    tag = int(m.group(1))

    out = []
    for li in _SS_SPIEL.findall(seite):
        kopf = re.match(r"<li[^>]*>", li).group(0)
        utc = re.search(r'data-datetime="([^"]+)"', kopf)
        heim = re.search(r'class="team-name team-name-home[^"]*">([^<]*)<', li)
        gast = re.search(r'class="team-name team-name-away[^"]*">([^<]*)<', li)
        if not (utc and heim and gast):
            continue
        # Ohne Uhrzeit ("--:--") steht der Anstoss noch nicht fest; dann gilt
        # nur das Datum.
        datum = (utc_to_berlin_str(utc.group(1)) if _SS_ZEIT.search(li)
                 else utc.group(1)[:10] + "T00:00:00")
        klassen = re.search(r'\bclass="([^"]*)"', kopf)
        beendet = bool(klassen) and "finished" in klassen.group(1).split()
        tore = [re.search(_SS_ERGEBNIS.format(seite_), li) for seite_ in ("home", "away")]
        fertig = beendet and all(tore)
        weiter = re.search(r'class="match-more">\s*<a href="([^"]+)"', li)
        out.append({
            "url": weiter.group(1) if weiter else None,
            "date": datum, "matchday": tag,
            "homeTeam": html_mod.unescape(heim.group(1)).strip(),
            "awayTeam": html_mod.unescape(gast.group(1)).strip(),
            "homeScore": int(tore[0].group(1)) if fertig else None,
            "awayScore": int(tore[1].group(1)) if fertig else None,
        })
    return tag, out


# Jede Seite hoechstens einmal je Lauf; der Cache gilt nur fuer diesen Prozess.
@functools.lru_cache(maxsize=16)
def _sportschau_html(pfad):
    req = urllib.request.Request(SPORTSCHAU_BASE + pfad, headers=SPORTSCHAU_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def sportschau_seite(competition, gender):
    """Uebersicht des aktuellen Spieltags einer Liga (eine Anfrage je Lauf)."""
    return _sportschau_html(SPORTSCHAU_QUELLEN[(competition, gender)])


def sportschau_abgleich(saison, fetched, competition, gender, log=None):
    """Holt die Uebersichtsseite und vergleicht. Eine nicht erreichbare oder
    nicht lesbare Seite ist ein Hinweis, kein Fehler - ein ausgefallener
    Abgleich darf den Lauf nicht scheitern lassen."""
    protokoll = log or (lambda s: None)
    if (competition, gender) not in SPORTSCHAU_QUELLEN or not fetched:
        return []
    try:
        seite = sportschau_seite(competition, gender)
    except (urllib.error.URLError, OSError) as e:
        protokoll(f"  Hinweis: Abgleich {competition}/{gender} {saison} nicht "
                  f"moeglich – Sportschau nicht erreichbar ({e}).")
        return []
    tag, zeilen = sportschau_spieltag(seite, saison)
    if not zeilen:
        protokoll(f"  Hinweis: Abgleich {competition}/{gender} {saison} nicht "
                  f"moeglich – auf der Sportschau-Seite keine Spiele dieser "
                  f"Saison erkannt (Layout geaendert?).")
        return []
    return abweichungen(fetched, zeilen)


# ---------- Sportschau: Torschuetzinnen der Frauen-Bundesliga ----------
#
# OpenLigaDB fuehrt bei den Frauen keine Torschuetzinnen, und eintracht-archiv.de
# traegt erst Tage spaeter nach. Der Liveticker eines beendeten Spiels bei
# sportschau.de nennt sie mit vollem Namen, Minute und Schussart.
#
# Das kostet je Spiel eine Seite (den Liveticker) und, wenn es nicht der
# aktuelle Spieltag ist, eine weitere, um den Link zu finden. Geholt wird ein
# Spiel einmal - danach steht die Torliste im Seed. Je Lauf gibt es hoechstens
# max_spiele Spiele, und nur beendete, deren Torliste fehlt.
#
# Uebernommen wird nur, was sich pruefen laesst: Die Tore je Mannschaft muessen
# dem Endstand entsprechen, und jede Schussart muss bekannt sein. Eigentore
# bleiben aussen vor, solange ungeklaert ist, welcher Seite Sportschau sie
# zuordnet - in den geprueften Spielen kam keines vor.

_SS_EREIGNISSE = re.compile(r'<div class="module-events">(.*?)<!--end module', re.S)
_SS_EREIGNIS = re.compile(r'<li class="event ([^"]*)">(.*?)</li>', re.S)
_SS_NAME = re.compile(r'class="person-name[^"]*">([^<]*)<')
_SS_MINUTE = re.compile(r'class="match_event-minute">(.*?)</div>', re.S)
# Schussarten, wie Sportschau sie als Klasse fuehrt ("head" = Kopfball).
_SS_SCHUSS = {"right-foot", "left-foot", "head", "header"}


def sportschau_tore(seite, heim_tore, gast_tore):
    """Torliste eines Livetickers in Seed-Form. -> (tore, problem)

    `problem` ist None oder der Grund, warum die Liste nicht taugt; dann ist
    `tore` leer. Geprueft wird gegen den Endstand (heim_tore, gast_tore).
    """
    block = _SS_EREIGNISSE.search(seite)
    if not block:
        return [], "keine Ereignisliste auf der Seite (Layout geaendert?)"
    tore = []
    for klassen, inhalt in _SS_EREIGNIS.findall(block.group(1)):
        teile = klassen.split()
        if "goal" not in teile:
            continue
        art = [t for t in teile if t not in ("goal", "home", "away")]
        text = _text(inhalt)
        eigentor = any("own" in t for t in art) or "igentor" in text
        if eigentor:
            return [], "Eigentor - Zuordnung zur Mannschaft ungeklaert"
        elfmeter = any(t.startswith("penalty") for t in art) or "lfmeter" in text
        if not elfmeter and not set(art) <= _SS_SCHUSS:
            return [], f"unbekannte Torart {' '.join(art)}"
        name = _SS_NAME.search(inhalt)
        minute = _SS_MINUTE.search(inhalt)
        zahlen = re.findall(r"\d+", _text(minute.group(1))) if minute else []
        if not name or not name.group(1).strip() or not zahlen:
            return [], "Tor ohne Namen oder Minute"
        if ("home" in teile) == ("away" in teile):
            return [], "Tor ohne eindeutige Mannschaft"
        tore.append({
            # Nachspielzeit "90" + "+2" wird addiert (92), wie OpenLigaDB sie zaehlt.
            "minute": sum(int(z) for z in zahlen[:2]),
            "scorer": _text(name.group(1)),
            "forHome": "home" in teile,
            "isPenalty": elfmeter,
            "isOwnGoal": False,
        })
    if (sum(t["forHome"] for t in tore), sum(not t["forHome"] for t in tore)) != (heim_tore, gast_tore):
        return [], f"{len(tore)} Tore im Liveticker passen nicht zum Endstand {heim_tore}:{gast_tore}"
    tore.sort(key=lambda t: t["minute"])
    for i, t in enumerate(tore):
        t["order"] = i
    return tore, None


def sportschau_torschuetzinnen(saison, kandidaten, competition, gender,
                               log=None, max_spiele=3, pause=1.0):
    """Traegt Torschuetzinnen fuer bereits bekannte, beendete Spiele nach.

    `kandidaten` ist {id: Spiel-Dict}. Zurueck kommen Kopien dieser Dicts mit
    gefuellter Torliste, sonst unveraendert - Datum, Vereinsnamen und Ergebnis
    bleiben also die des Seeds, und der Upsert kann nichts ausser den Toren
    anfassen.

    Zugeordnet wird ueber Spieltag, Endstand und Vereinsname (mindestens eine
    Seite stimmt ueberein, die Eintracht ist immer dabei).
    """
    protokoll = log or (lambda s: None)
    pfad = SPORTSCHAU_QUELLEN.get((competition, gender))
    if not pfad or not kandidaten:
        return []
    uebersicht = sportschau_seite(competition, gender)
    aktuell, aktuelle_spiele = sportschau_spieltag(uebersicht, saison)
    if aktuell is None:
        protokoll(f"  Hinweis: Sportschau {competition}/{gender} {saison} – "
                  f"Spieltag nicht erkannt, keine Torschuetzinnen.")
        return []
    stamm = pfad.rsplit("/spiele-und-ergebnisse", 1)[0]
    spieltag_pfad = re.search(
        re.escape(stamm) + r"/(se\d+/\d{4}-\d{4}/ro\d+/spieltag)/md\d+/spiele-und-ergebnisse",
        uebersicht)

    out, offen = [], 0
    for mid, m in sorted(kandidaten.items(), key=lambda kv: kv[1]["date"]):
        if len(out) >= max_spiele:
            offen += 1
            continue
        tag = m.get("matchday")
        if tag is None:
            continue
        if tag == aktuell:
            spiele = aktuelle_spiele
        elif spieltag_pfad:
            try:
                seite = _sportschau_html(f"{stamm}/{spieltag_pfad.group(1)}/md{tag}/spiele-und-ergebnisse")
            except (urllib.error.URLError, OSError) as e:
                protokoll(f"  Hinweis: Sportschau Spieltag {tag} nicht abrufbar – {e}")
                continue
            time.sleep(pause)
            _, spiele = sportschau_spieltag(seite, saison)
        else:
            protokoll(f"  Hinweis: Sportschau – kein Link zum {tag}. Spieltag gefunden.")
            continue

        stand = (m["homeScore"], m["awayScore"])
        treffer = [f for f in spiele if f.get("url")
                   and (f["homeScore"], f["awayScore"]) == stand
                   and (slug(f["homeTeam"]) == slug(m["homeTeam"])
                        or slug(f["awayTeam"]) == slug(m["awayTeam"]))]
        if len(treffer) != 1:
            protokoll(f"  Hinweis: {mid} – {len(treffer)} passende Spiele bei Sportschau "
                      f"({tag}. Spieltag, {stand[0]}:{stand[1]}).")
            continue
        ticker = treffer[0]["url"].rsplit("/", 1)[0] + "/liveticker"
        try:
            seite = _sportschau_html(ticker)
        except (urllib.error.URLError, OSError) as e:
            protokoll(f"  Hinweis: Sportschau {mid} nicht abrufbar – {e}")
            continue
        time.sleep(pause)
        tore, problem = sportschau_tore(seite, *stand)
        if problem:
            protokoll(f"  PRUEFEN: {mid} Torschuetzinnen nicht uebernommen – {problem}")
            continue
        angereichert = dict(m)
        angereichert["goals"] = tore
        angereichert["goalsLoaded"] = True
        out.append(angereichert)
        protokoll(f"  Sportschau {mid}: {len(tore)} Tore "
                  f"({m['homeTeam']} – {m['awayTeam']} {stand[0]}:{stand[1]})")
    if offen:
        protokoll(f"  Sportschau: {offen} Spiele bleiben offen "
                  f"(Grenze {max_spiele} Spiele je Lauf).")
    return out
