#!/usr/bin/env python3
"""Live Match Tactics Agent — playback entrypoint.

Streams the cleaned StatsBomb event file through the three-agent pipeline and
prints the tactical timeline as the match "plays":

    Agent 1  State Tracker      (agent/state.py)     running match state
    Agent 2  Tactical Reasoner  (agent/reasoner.py)  significance + reasoning
    Agent 3  Narrator           (agent/narrator.py)  scout-voice commentary

Usage
-----
  python playback.py                     # auto: LLM if a key is set, else mock
  python playback.py --mock              # force the deterministic fallback
  python playback.py --pace 0            # instant (whole match, no sleeping)
  python playback.py --pace 1.5          # demo pace: 1.5 s per match-minute
  python playback.py --end-minute 60     # partial run (dev / tests)

The --pace loop is what makes a post-match analysis *feel* live (brief §2:
"simulate it as live by streaming pre-recorded event data at real-time pace").
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.llm import LLMClient, available_provider          # noqa: E402
from agent.mock import mock_verdict                          # noqa: E402
from agent.narrator import Narrator                          # noqa: E402
from agent.reasoner import Reasoner                          # noqa: E402
from agent.state import MatchState, iter_match, total_match_minutes  # noqa: E402

DEFAULT_DATA = Path(__file__).resolve().parent / "data" / "match.json"


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="cleaned match json (default data/match.json)")
    ap.add_argument("--mock", action="store_true", help="force the rule-based fallback (no LLM calls)")
    ap.add_argument("--llm", action="store_true", help="require an LLM key (fail fast if none)")
    ap.add_argument("--pace", type=float, default=0.0, metavar="SEC",
                    help="seconds of wall clock per match-minute; 0 = instant (default 0)")
    ap.add_argument("--start-minute", type=int, default=0)
    ap.add_argument("--end-minute", type=int, default=10_000)
    ap.add_argument("--quiet", action="store_true", help="only print the timeline entries")
    return ap.parse_args(argv)


def build_agents(args):
    provider = available_provider()
    if args.mock or (provider is None and not args.llm):
        reasoner = Reasoner(client=None, mock=mock_verdict)
        narrator = Narrator(client=None)
        mode = "MOCK (rule-based fallback — no API key used)"
    elif provider is None and args.llm:
        raise SystemExit("--llm requested but no API key found (set GEMINI_API_KEY or ANTHROPIC_API_KEY)")
    else:
        client = LLMClient(provider=provider)
        reasoner = Reasoner(client=client)
        narrator = Narrator(client=client)
        mode = f"LLM ({provider})"
    return reasoner, narrator, mode


def run(match: dict, reasoner: Reasoner, narrator: Narrator, args) -> list[dict]:
    state = MatchState(match)
    timeline: list[dict] = []
    pace = args.pace
    clock_prev = 0.0
    t0 = time.monotonic()

    if not args.quiet:
        print(f"\n=== {match['home_team']} vs {match['away_team']} — {match['competition']} "
              f"{match['season']}, {match['match_date']} ===")
        print(f"mode: {reasoner.__class__.__name__}"
              f"{' (keyless fallback)' if reasoner.client is None else ''}")

    for event in iter_match(match):
        clock = event.get("minute", 0) + event.get("second", 0) / 60.0
        # pace only inside the requested window — fast-forward into it and out of it
        if pace > 0 and clock > clock_prev and args.start_minute <= clock <= args.end_minute:
            time.sleep(min((clock - clock_prev) * pace, 8.0))
        clock_prev = max(clock_prev, clock)
        if clock < args.start_minute or clock > args.end_minute:
            state.apply(event)
            continue

        state.apply(event)

        if not reasoner.is_candidate(event):
            continue
        verdict = reasoner.judge(event, state)
        if not verdict.get("significant"):
            continue

        line = narrator.narrate(verdict, event, state.snapshot())
        entry = {
            "minute": event.get("minute"),
            "clock": f"{event.get('minute')}'",
            "score": f"{state.score[state.home]}-{state.score[state.away]}",
            "category": verdict.get("category"),
            "weight": verdict.get("score"),
            "team": event.get("team"),
            "commentary": line,
            "reasoning": verdict.get("reasoning"),
            "tactical_meaning": verdict.get("tactical_meaning"),
            "watch_for": verdict.get("watch_for"),
        }
        timeline.append(entry)

        marker = {"goal": "⚽", "red_card": "🟥", "penalty": "❗", "formation": "⟐",
                  "substitution": "⇄", "big_chance": "△", "momentum_swing": "≈"}.get(entry["category"], "•")
        print(f"\n{entry['clock']:>4} [{entry['score']}] {marker} {entry['team']}: {line}")
        if not args.quiet:
            if entry.get("reasoning"):
                print(f"      └─ why: {entry['reasoning']}")
            if entry.get("watch_for"):
                print(f"         watch: {entry['watch_for']}")

    elapsed = time.monotonic() - t0
    if not args.quiet:
        print(f"\n=== FULL TIME {state.score[state.home]}-{state.score[state.away]} "
              f"({match['home_team']} {state.score[state.home]} - {state.score[state.away]} {match['away_team']}) ===")
        print(f"events applied: {state.events_applied()}")
        print(f"reasoner: {reasoner.stats}")
        print(f"narrator: {narrator.stats}")
        print(f"timeline entries: {len(timeline)}  |  wall time: {elapsed:.1f}s")
    return timeline


def main() -> int:
    _force_utf8_stdio()
    args = parse_args()
    data_path = Path(args.data)
    if not data_path.exists():
        raise SystemExit(f"no match data at {data_path} — run: python fetch_data.py --match 266033 --matches-file 11/2")
    match = json.loads(data_path.read_text(encoding="utf-8"))
    if not args.quiet:
        total = total_match_minutes(match)
        print(f"loaded {match['meta']['cleaned_events']} events "
              f"({total} match-minutes) from {data_path.name}")

    reasoner, narrator, mode = build_agents(args)
    if not args.quiet:
        print(f"agent mode: {mode}")

    timeline = run(match, reasoner, narrator, args)

    out = Path(__file__).resolve().parent / "cache" / "timeline_last_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": mode, "timeline": timeline}, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.quiet:
        print(f"saved: {out.relative_to(Path(__file__).resolve().parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
