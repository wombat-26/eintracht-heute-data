#!/usr/bin/env python3
"""Datenquellen: OpenLigaDB (saisonweise), ESPN (tagesweise) und das
DFB-Datencenter (Torschuetzinnen der Frauen-Bundesliga).

Bildet OpenLigaDBProvider.swift und ESPNProvider.swift 1:1 nach - inklusive
Teamnamen-Normalisierung, ID-Erzeugung und der Auswahl des Endergebnisses.
Weicht das Python hier ab, driften Server- und Geraetestand auseinander.

Das Datencenter hat bewusst kein Swift-Gegenstueck: Es ist HTML, kein JSON,
und gehoert damit in die Pipeline, nicht aufs Geraet.
"""
import json, re, time, urllib.request, urllib.error
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


def _parse_goals(goals, roster=None):
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
# Zweitquelle, ausschliesslich fuer Torschuetzinnen der Frauen-Bundesliga.
#
# Warum ueberhaupt: OpenLigaDB liefert fuer ffb1 seit 2026 nur noch
# Ergebnisse. Namen und Minuten muessten sonst von Hand nachgetragen werden.
# Geprueft und verworfen wurden ESPN (kennt gar keine deutsche Frauenliga -
# nur eng.w.1, esp.w.1, fra.w.1, ned.w.1, aus.w.1 und die UWCL),
# TheSportsDB (fuer Deutschland nur Bundesliga, 2. Bundesliga, DFB-Pokal und
# DFB-Pokal Frauen) sowie Sofascore (403 fuer alles, was nicht wie ein
# Browser aussieht). api-football.com kann es, aber der Free-Plan ist auf
# aeltere Saisons beschraenkt.
#
# Zwei Einschraenkungen, die den Zuschnitt dieses Providers bestimmen:
#
#  1. Es ist HTML-Scraping. Ein Redesign beim DFB legt den Parser still.
#     Deshalb faellt der Ausfall hier immer weich aus - fehlende
#     Torschuetzinnen sind kein Grund, den Lauf rot zu faerben (Regel 4
#     zielt auf kaputte Daten, nicht auf fehlende Ergaenzungen).
#  2. Der Wettbewerbs-Slug traegt den Sponsornamen. Vor Google Pixel war es
#     Flyeralarm, und der naechste Vertrag aendert ihn wieder.
#
# Wichtigste Eigenschaft: Dieser Provider legt NIE ein Spiel an. Er sieht
# nur Partien, die ohnehin schon im Seed stehen, und gibt sie unveraendert
# mit gefuellter Torliste zurueck. Damit kann eine abweichende
# Vereinsschreibweise im Datencenter keine Dublette erzeugen - der schlimmste
# Fall ist ein Spiel ohne Namen.

DFB_BASE = "https://datencenter.dfb.de"
DFB_HEADERS = {
    "User-Agent": UA["User-Agent"],
    "Accept": "text/html, application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}
# Ohne diesen Header rendert der Rails-Server den Spielplan nicht aus,
# sondern laedt ihn per Turbo-Frame nach - die Antwort waere leer.
DFB_TURBO = {"Turbo-Frame": "spielplan"}

# Reihenfolge = Rateversuch. Neuer Sponsor: vorne ergaenzen, alte Eintraege
# stehen lassen (aeltere Saisons liegen weiterhin unter ihrem Slug).
DFB_FRAUEN_WETTBEWERBE = ("google-pixel-frauen-bundesliga", "frauen-bundesliga")
DFB_TEAM_FRAUEN = "eintracht-frankfurt-31426"


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


def dfb_spielplan_seite(saison, wettbewerbe=DFB_FRAUEN_WETTBEWERBE,
                        team=DFB_TEAM_FRAUEN):
    """Holt die Vereinsspielplan-Seite einer Saison. -> (html, url) oder (None, None).

    Der Saison-Slug wird nicht gebaut, sondern erraten und dann korrigiert:
    Beim ersten Versuch steht der heute uebliche Aufbau
    "<wettbewerb>-<jahr>-<jahr+1>". Schlaegt der fehl, liefert der DFB
    trotzdem eine Seite - und die enthaelt im Saison-Auswahlfeld die
    tatsaechlich gueltigen Slugs. Aus denen wird der passende nachgezogen.
    Das faengt Formatwechsel ab: Bis 2022/23 hiessen sie schlicht "2022-23".
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
        })
    return out


_DFB_BLOCK = re.compile(r'<div class="m-MatchDetails-history">(.*?)(?=<div class="m-MatchDetails-history">|\Z)', re.S)
_DFB_TITEL = re.compile(r'm-MatchDetails-history-title[^>]*>(.*?)</div>', re.S)
_DFB_ITEM = re.compile(r'm-MatchDetails-history-item">(.*?)(?=m-MatchDetails-history-item">|\Z)', re.S)
_DFB_SEITE = re.compile(r'm-MatchDetails-history-event--(home|away)( is-empty)?"')
_DFB_SEGMENT = re.compile(r'm-MatchDetails-history-event-text-segment">(.*?)</div>', re.S)
_DFB_MINUTE = re.compile(r"m-MatchDetails-history-minute\">\s*(\d+)")


def dfb_tore(spiel_html):
    """Torliste einer Spielschema-Seite in Seed-Form.

    Aufbau der Quelle: Ein Block je Ereignisart ("Tore", "Karten",
    "Wechsel"), darin ein Item je Ereignis mit zwei Spalten. Die Spalte der
    unbeteiligten Mannschaft traegt `is-empty` - daraus ergibt sich forHome,
    ohne die Vereinsnamen vergleichen zu muessen.

    Elfmeter und Eigentore stehen als Klammerzusatz im Text ("(Elfmeter)"),
    nicht als eigenes Symbol: Alle Tore nutzen dasselbe Icon
    `m-MatchDetails-icon-goal--goal` (ueber 14 Spiele der Saison 2025/26
    geprueft, 122 Tore, keine andere Variante).

    Zum Eigentor: In den geprueften Spielen kam keines vor, die Zuordnung
    von forHome ist dort also ungetestet. dfb_frauen_bundesliga() meldet
    jedes gefundene Eigentor im Protokoll, damit der erste echte Fall
    auffaellt statt still falsch zu landen.
    """
    block = None
    for kandidat in _DFB_BLOCK.findall(spiel_html):
        titel = _DFB_TITEL.search(kandidat)
        if titel and _text(titel.group(1)).lower().startswith("tore"):
            block = kandidat
            break
    if not block:
        return []

    out = []
    for item in _DFB_ITEM.findall(block):
        seiten = _DFB_SEITE.findall(item)
        aktiv = [s for s, leer in seiten if not leer]
        if len(aktiv) != 1:
            continue
        seg = _DFB_SEGMENT.search(item)
        if not seg:
            continue
        roh = seg.group(1)
        name = _A_TEXT.search(roh)
        text = _text(roh)
        # Ohne Verlinkung (kommt bei Spielerinnen ohne DFB-Profil vor) bleibt
        # nur der Text - Spielstand und Klammerzusatz herausschneiden.
        if name:
            scorer = _text(name.group(1))
        else:
            scorer = re.sub(r"\(.*?\)|\d+\s*:\s*\d+", " ", text).strip()
        minute = _DFB_MINUTE.search(item)
        out.append({
            # Nachspielzeit steht als "90+3" - die erste Zahl ist die, die
            # auch der Seed fuehrt.
            "minute": int(minute.group(1)) if minute else None,
            "scorer": scorer or "–",
            "forHome": aktiv[0] == "home",
            "isPenalty": "lfmeter" in text,
            "isOwnGoal": "igentor" in text,
            "order": len(out),
        })
    return out


def dfb_frauen_bundesliga(saison, kandidaten, log=None, max_detail=10,
                          wettbewerbe=DFB_FRAUEN_WETTBEWERBE,
                          team=DFB_TEAM_FRAUEN, pause=0.8):
    """Traegt Torschuetzinnen fuer bereits bekannte Spiele nach.

    `kandidaten` ist {id: Spiel-Dict} und enthaelt genau die Partien, denen
    Tore fehlen. Zurueck kommen Kopien dieser Dicts mit gefuellter Torliste,
    sonst unveraendert - Datum, Vereinsnamen und Ergebnis bleiben also die
    des Seeds. Dadurch kann der Upsert nichts ausser den Toren anfassen und
    ein abweichend geschriebener Vereinsname erzeugt keine Aenderung, die
    sich bei jedem Lauf hin und her schiebt.

    `max_detail` begrenzt die Detailseiten pro Lauf. Ein Rueckstand
    verteilt sich damit ueber mehrere Laeufe, statt das Datencenter in einem
    Schwung mit 26 Anfragen zu belegen.
    """
    protokoll = log or (lambda s: None)
    if not kandidaten:
        return []

    seite, quelle = dfb_spielplan_seite(saison, wettbewerbe, team)
    if not seite:
        protokoll(f"  Hinweis: DFB-Datencenter – kein Spielplan fuer {saison} "
                  f"gefunden. Wettbewerbs-Slug in DFB_FRAUEN_WETTBEWERBE pruefen.")
        return []
    protokoll(f"  DFB-Datencenter: {quelle}")

    out, offen = [], 0
    for f in dfb_fixtures(seite):
        if not f["url"] or f["homeScore"] is None:
            continue
        mid = make_id(f["date"], f["homeTeam"], f["awayTeam"])
        ziel = kandidaten.get(mid)
        if ziel is None:
            continue
        if len(out) >= max_detail:
            offen += 1
            continue
        try:
            tore = dfb_tore(_dfb_html(f["url"]))
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            protokoll(f"  Hinweis: DFB {f['dfbId']} nicht abrufbar – {e}")
            continue
        time.sleep(pause)
        if not tore:
            continue
        angereichert = dict(ziel)
        angereichert["goals"] = tore
        angereichert["goalsLoaded"] = True
        out.append(angereichert)
        protokoll(f"  DFB {mid}: {len(tore)} Tore "
                  f"({f['homeTeam']} – {f['awayTeam']} {f['homeScore']}:{f['awayScore']})")
        if any(g["isOwnGoal"] for g in tore):
            protokoll(f"  PRUEFEN: {mid} enthaelt ein Eigentor – Zuordnung "
                      f"forHome ist fuer diesen Fall ungetestet.")
    if offen:
        protokoll(f"  DFB-Datencenter: {offen} Spiele bleiben offen "
                  f"(Grenze {max_detail} Detailseiten je Lauf).")
    return out
