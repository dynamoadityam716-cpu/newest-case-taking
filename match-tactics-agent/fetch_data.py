#!/usr/bin/env python3
"""Fetch + clean StatsBomb open data for the Live Match Tactics Agent.

Produces the compact ``data/match.json`` the agents consume, so the demo
itself never touches the network (brief §8: pre-download everything).

DATA SOURCE NOTE (2026): the StatsBomb open-data repository was migrated to
https://github.com/hudl/open-data. The legacy ``statsbomb/open-data`` raw
URLs 404 for most files now, so this script pins to ``hudl/open-data@master``.

Usage
-----
  # see every match in a season file (competition/season ids)
  python fetch_data.py --list --matches-file 11/2

  # find which season file contains a match id
  python fetch_data.py --search 266033 --competition 11

  # fetch + clean a match into data/match.json (committed demo default)
  python fetch_data.py --match 266033 --matches-file 11/2

  # fetch into a different output (kept out of git, see .gitignore)
  python fetch_data.py --match 267569 --matches-file 11/2 --out data/el_clasico.json

Stdlib only — no statsbombpy, no pip install needed.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _force_utf8_stdio() -> None:
    """Windows consoles default to cp1252 and crash on non-Latin team names
    and on non-ASCII paths (e.g. localized Documents folders)."""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

REPO = "hudl/open-data"
BRANCH = "master"
BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/data/"
DEFAULT_MATCHES_FILE = "11/2"  # La Liga 2016/2017 — contains the demo match
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "match.json"
RAW_CACHE = Path(__file__).resolve().parent / "data" / "raw"  # git-ignored

UA = {"User-Agent": "match-tactics-agent-fetch/0.1 (educational hackathon use)"}
_ATTEMPTS = 3


def get_json(path: str):
    """GET BASE+path with retries; caches raw bytes under data/raw/."""
    cache = RAW_CACHE / path.replace("/", "__")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    url = BASE + path
    last_err: Exception | None = None
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=120, context=ssl.create_default_context()) as resp:
                blob = resp.read()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(blob)
            return json.loads(blob.decode("utf-8"))
        except Exception as err:  # noqa: BLE001 — network noise is expected
            last_err = err
            wait = 2 * attempt
            print(f"  ! attempt {attempt}/{_ATTEMPTS} failed for {path}: {err}; retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"could not fetch {url}: {last_err}") from last_err


def load_matches_file(matches_file: str) -> list[dict]:
    return get_json(f"matches/{matches_file}.json")


def list_matches(matches: list[dict]) -> None:
    print(f"{'id':>8}  {'date':<10}  match")
    for m in sorted(matches, key=lambda x: x["match_date"]):
        home, away = m["home_team"], m["away_team"]
        print(
            f"{m['match_id']:>8}  {m['match_date']:<10}  "
            f"{home['home_team_name']} {m['home_score']}-{m['away_score']} {away['away_team_name']}"
        )


def search_match(match_id: int, competition: int | None) -> tuple[str, dict] | None:
    """Scan competitions.json → season match files until the id turns up."""
    comps = get_json("competitions.json")
    pairs = [(c["competition_id"], c["season_id"], f"{c['competition_name']} {c['season_name']}")
             for c in comps if competition is None or c["competition_id"] == competition]
    print(f"searching {len(pairs)} competition/season files for match {match_id} …")
    for cid, sid, label in pairs:
        try:
            matches = load_matches_file(f"{cid}/{sid}")
        except RuntimeError:
            continue
        for m in matches:
            if m["match_id"] == match_id:
                print(f"  found in {label} ({cid}/{sid})")
                return f"{cid}/{sid}", m
        print(f"  … not in {label}", file=sys.stderr)
    return None


def _location(e: dict) -> list | None:
    loc = e.get("location")
    return [round(loc[0], 1), round(loc[1], 1)] if loc else None


def _outcome(e: dict) -> str | None:
    for key in ("shot", "pass", "dribble", "goalkeeper", "interception", "clearance",
                "50_50", "pressure", "block", "miscontrol", "foul_committed", "foul_won"):
        out = (e.get(key) or {}).get("outcome")
        if out:
            return out["name"]
    return None


def clean_event(e: dict) -> dict | None:
    """One raw StatsBomb event → compact dict (drop empties); None to skip."""
    etype = e["type"]["name"]
    if etype in ("Half Start", "Half End", "Camera On", "Camera Off", "Own Goal Against", "Own Goal For"):
        return None
    out: dict = {
        "i": e["index"],
        "p": e["period"],
        "minute": e["minute"],
        "second": e.get("second", 0),
        "type": etype,
        "team": e["team"]["name"],
    }
    if e.get("player"):
        out["player"] = e["player"]["name"]

    loc = _location(e)
    if loc and etype in ("Pass", "Shot", "Carry", "Dribble", "Pressure"):
        out["location"] = loc
    if etype == "Pass" and e.get("pass"):
        p = e["pass"]
        end = p.get("end_location")
        if end:
            out["end_location"] = [round(end[0], 1), round(end[1], 1)]
        if p.get("through_ball"):
            out["through_ball"] = True
        if p.get("switch"):
            out["switch"] = True
        if p.get("cross"):
            out["cross"] = True
        if p.get("assisted_shot_id"):
            out["key_pass"] = True
        if p.get("height"):
            out["pass_height"] = p["height"]["name"]
    if etype == "Shot" and e.get("shot"):
        s = e["shot"]
        if s.get("statsbomb_xg"):
            out["xg"] = round(float(s["statsbomb_xg"]), 3)
        if s.get("type"):
            out["shot_type"] = s["type"]["name"]
        if s.get("one_on_one"):
            out["one_on_one"] = True
    if etype == "Carry" and e.get("carry", {}).get("end_location"):
        end = e["carry"]["end_location"]
        out["end_location"] = [round(end[0], 1), round(end[1], 1)]

    oc = _outcome(e)
    if oc:
        out["outcome"] = oc

    card = (e.get("bad_behaviour") or {}).get("card")
    if card:
        out["card"] = card["name"]
        if e.get("bad_behaviour", {}).get("reason"):
            out["card_reason"] = e["bad_behaviour"]["reason"]

    if etype == "Substitution" and e.get("substitution"):
        sub = e["substitution"]
        out["player_off"] = e.get("player", {}).get("name")
        out["player_in"] = sub.get("replacement", {}).get("name")
        if sub.get("outcome"):
            out["sub_reason"] = sub["outcome"]["name"]

    if etype in ("Starting XI", "Tactical Shift") and e.get("tactics"):
        t = e["tactics"]
        out["formation"] = t.get("formation")
        # 2026 hudl snapshot renamed player_lineup → lineup; support both.
        lineup = t.get("lineup") or t.get("player_lineup") or []
        out["lineup_count"] = len(lineup)

    if e.get("under_pressure"):
        out["under_pressure"] = True
    return out


def clean_match(raw_events: list[dict], match_meta: dict) -> dict:
    events = sorted(
        raw_events,
        key=lambda e: (e.get("period", 0), e.get("minute", 0), e.get("second", 0), e.get("index", 0)),
    )
    cleaned = [c for c in (clean_event(e) for e in events) if c]

    def start_form(team_id: int) -> str | None:
        for e in raw_events:
            if e["type"]["name"] == "Starting XI" and e["team"]["id"] == team_id:
                return (e.get("tactics") or {}).get("formation")
        return None

    home, away = match_meta["home_team"], match_meta["away_team"]
    return {
        "match_id": match_meta["match_id"],
        "competition": match_meta["competition"]["competition_name"],
        "season": match_meta["season"]["season_name"],
        "stage": match_meta.get("competition_stage", {}).get("name"),
        "match_date": match_meta["match_date"],
        "match_week": match_meta.get("match_week"),
        "home_team": home["home_team_name"],
        "away_team": away["away_team_name"],
        "home_score": match_meta["home_score"],
        "away_score": match_meta["away_score"],
        "home_formation_start": start_form(home["home_team_id"]),
        "away_formation_start": start_form(away["away_team_id"]),
        "events": cleaned,
        "meta": {
            "source": f"github.com/{REPO}@{BRANCH}",
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "raw_events": len(raw_events),
            "cleaned_events": len(cleaned),
        },
    }


def summarize(m: dict) -> None:
    print(f"\ncleaned: {m['home_team']} {m['home_score']}-{m['away_score']} {m['away_team']}"
          f"  [{m['competition']} {m['season']}, {m['match_date']}]")
    print(f"events kept: {m['meta']['cleaned_events']} / {m['meta']['raw_events']} raw")
    for e in m["events"]:
        if e["type"] == "Shot" and e.get("outcome") == "Goal":
            print(f"  GOAL  {e['minute']:>3}'  {e['team']}  {e.get('player','?')} (xG {e.get('xg','-')})")
        if e.get("card") in ("Red Card", "Second Yellow"):
            print(f"  CARD  {e['minute']:>3}'  {e['team']}  {e.get('player','?')} — {e['card']}")
        if e["type"] == "Substitution":
            print(f"  SUB   {e['minute']:>3}'  {e['team']}  {e.get('player_in','?')} ⇠ {e.get('player_off','?')}")
        if e["type"] == "Tactical Shift":
            print(f"  FORM  {e['minute']:>3}'  {e['team']}  → {e.get('formation')}")


def main() -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--match", type=int, help="match id to fetch + clean")
    ap.add_argument("--matches-file", default=DEFAULT_MATCHES_FILE,
                    help=f"competition/season id pair (default {DEFAULT_MATCHES_FILE})")
    ap.add_argument("--list", action="store_true", help="list matches in --matches-file and exit")
    ap.add_argument("--search", action="store_true", help="find which season file holds --match")
    ap.add_argument("--competition", type=int, default=None, help="limit --search to one competition id")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="output path (default data/match.json)")
    args = ap.parse_args()

    if args.list:
        list_matches(load_matches_file(args.matches_file))
        return 0

    if args.search:
        if not args.match:
            ap.error("--search needs --match <id>")
        hit = search_match(args.match, args.competition)
        if not hit:
            print("not found")
            return 1
        print(f"re-run with:  python fetch_data.py --match {args.match} --matches-file {hit[0]}")
        return 0

    if not args.match:
        ap.error("nothing to do — pass --match (or --list / --search)")

    matches = load_matches_file(args.matches_file)
    meta = next((m for m in matches if m["match_id"] == args.match), None)
    if not meta:
        print(f"match {args.match} not in {args.matches_file} — try --search {args.match}", file=sys.stderr)
        return 1

    print(f"fetching events for match {args.match} (this can take a moment, ~3-7 MB) …")
    raw_events = get_json(f"events/{args.match}.json")
    cleaned = clean_match(raw_events, meta)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cleaned, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out_path}  ({out_path.stat().st_size / 1e6:.2f} MB)")
    summarize(cleaned)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
