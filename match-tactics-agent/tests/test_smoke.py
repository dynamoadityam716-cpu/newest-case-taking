#!/usr/bin/env python3
"""Smoke tests — plain python, no framework needed:

    python tests/test_smoke.py

Covers the behaviors that must never regress: Agent 1 state tracking (score,
formations, momentum bounds), Agent 2 nomination + verdict hardening (stub LLM
clients: clean JSON, prose-wrapped JSON, garbage, lazy types, explosions),
Agent 3 fallback narration, and one full-match instant end-to-end run.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from agent.llm import _extract_json, LLMError  # noqa: E402
from agent.mock import mock_verdict            # noqa: E402
from agent.narrator import Narrator            # noqa: E402
from agent.reasoner import Reasoner            # noqa: E402
from agent.state import MatchState, iter_match # noqa: E402

MATCH_PATH = ROOT / "data" / "match.json"


def _load_match() -> dict:
    if not MATCH_PATH.exists():
        print(f"SKIP: {MATCH_PATH} missing — run fetch_data.py first")
        sys.exit(0)
    return json.loads(MATCH_PATH.read_text(encoding="utf-8"))


class StubOK:
    @staticmethod
    def complete_json(_s, _u):
        return {"significant": True, "score": 8, "category": "substitution",
                "reasoning": "Trailing side changes a full-back.",
                "tactical_meaning": "More width high.", "watch_for": "Overlap next 10 min."}


class StubProse:
    @staticmethod
    def complete_json(_s, _u):
        return ('Here you go:\n```json\n{"significant": false, "score": 2, "category": "noise", '
                '"reasoning": "routine", "tactical_meaning": "", "watch_for": ""}\n```')


class StubBoom:
    @staticmethod
    def complete_json(_s, _u):
        raise RuntimeError("429 exploded")


class StubLazy:
    @staticmethod
    def complete_json(_s, _u):
        return {"significant": "true", "score": "77"}  # wrong types on purpose


class StubGarbage:
    @staticmethod
    def complete_json(_s, _u):
        return "the model rambled with no JSON at all"


def test_extract_json():
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('prose ```json\n{"a": 2}\n```') == {"a": 2}
    assert _extract_json('noise {"a": 3} trailing') == {"a": 3}
    try:
        _extract_json("no json here")
        raise AssertionError("expected LLMError")
    except LLMError:
        pass
    print("ok  extract_json")


def test_state():
    match = _load_match()
    state = MatchState(match)
    for e in iter_match(match):
        state.apply(e)
    assert state.score[state.home] == match["home_score"]
    assert state.score[state.away] == match["away_score"]
    assert state.events_applied() == match["meta"]["cleaned_events"]
    # Valencia are the HOME side (Mestalla) — their in-match Tactical Shifts apply
    assert state.formations[state.home] == "4231"
    assert state.formations[state.away] == "433"  # Barcelona never reshaped in-match
    assert -1.0 <= state.momentum() <= 1.0
    snap = state.snapshot()
    assert snap["momentum_reading"]
    assert snap["shot_totals"][match["home_team"]]["goals"] == match["home_score"]
    print("ok  state tracker (score, formations, momentum, shot totals)")


def test_reasoner_nominations():
    match = _load_match()
    r = Reasoner(client=None, mock=mock_verdict)
    nominations = [e for e in iter_match(match) if r.is_candidate(e)]
    assert nominations, "rules must nominate candidates"
    assert all(e["type"] in ("Shot", "Substitution", "Tactical Shift", "Penalty", "Foul")
               or e.get("card") for e in nominations)
    # a Pass carrier must never reach the LLM
    assert not any(e["type"] == "Pass" for e in nominations)
    print(f"ok  nomination layer ({len(nominations)} candidates of {match['meta']['cleaned_events']} events)")


def test_verdict_hardening():
    match = _load_match()
    state = MatchState(match)
    events = list(iter_match(match))
    sub = next(e for e in events if e["type"] == "Substitution")
    shot = next(e for e in events if e["type"] == "Shot")
    for e in events[:200]:
        state.apply(e)

    v1 = Reasoner(client=StubOK()).judge(sub, state)
    assert v1["significant"] is True and v1["score"] == 8

    v2 = Reasoner(client=StubProse()).judge(shot, state)
    assert v2["significant"] is False  # JSON rescued from prose fence

    v3 = Reasoner(client=StubBoom()).judge(shot, state)
    assert v3["significant"] is False and "reasoner unavailable" in v3["reasoning"]

    v4 = Reasoner(client=StubLazy()).judge(shot, state)
    assert v4["significant"] is True and v4["score"] == 10  # "77" clamped

    v5 = Reasoner(client=StubGarbage()).judge(shot, state)
    assert v5["significant"] is False
    print("ok  verdict hardening (ok/prose/boom/lazy/garbage stubs)")


def test_narrator_fallback():
    match = _load_match()
    state = MatchState(match)
    events = list(iter_match(match))
    goal = next(e for e in events if e["type"] == "Shot" and e.get("outcome") == "Goal")
    for e in events:
        if e.get("minute", 0) > goal["minute"]:
            break
        state.apply(e)
    verdict = mock_verdict(goal, state.snapshot())
    assert verdict["significant"] is True
    line = Narrator(client=None).narrate(verdict, goal, state.snapshot())
    assert line and "GOAL" in line
    assert not line.startswith(f"{goal['minute']}")
    quiet = Narrator(client=None).narrate({"significant": False}, goal, state.snapshot())
    assert quiet == ""
    print("ok  narrator fallback (scout line, empty on insignificant)")


def test_end_to_end_instant():
    import io
    import contextlib
    import playback

    argv = playback.parse_args(["--mock", "--quiet"])
    match = _load_match()
    reasoner, narrator, mode = playback.build_agents(argv)
    assert "MOCK" in mode
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        timeline = playback.run(match, reasoner, narrator, argv)
    assert len(timeline) >= 10
    categories = {t["category"] for t in timeline}
    assert {"goal", "substitution", "formation"} <= categories
    goals = [t for t in timeline if t["category"] == "goal"]
    assert len(goals) == match["home_score"] + match["away_score"]
    print(f"ok  end-to-end instant run ({len(timeline)} entries, {sorted(categories)})")


def test_server_surface():
    """The real HTTP surface: MatchServer + time-gated Broadcast."""
    import threading
    import time as _t
    import urllib.request
    import playback

    match = _load_match()
    args = playback.parse_args(["--mock", "--pace", "0.4"])
    payload = playback.build_replay_cache(match, args)
    # re-load the cache from disk and shrink its reveal times so the test can
    # observe time-gating without waiting — mutations go through the FILE,
    # mirroring the committed format (entries keep their t stamps in cache)
    cache_file = playback.cache_path_for(match)
    payload = json.loads(cache_file.read_text(encoding="utf-8"))
    for e in payload["timeline"]:
        e["t"] = 0.02
    for s in payload["state_trail"]:
        s["t"] = 0.005
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    payload = playback.load_replay_cache(match, args)
    broadcast = playback._broadcast_from_cache(match, payload, args)
    assert broadcast.counts()[0] == 13
    # same-pace load must honor the file's (shrunken) stamps verbatim
    assert broadcast.entries[0]["t"] == 0.02
    # a different serve pace re-stamps from match minutes (cadence adjustable)
    restamped = playback._broadcast_from_cache(match, payload, playback.parse_args(["--mock", "--pace", "0.8"]))
    assert restamped.entries[-1]["t"] == round(restamped.entries[-1]["minute"] * 0.8 + 1.0, 3)
    # deterministic full-time flip: 0.6s after broadcast creation
    broadcast.finish_t = 0.6
    _t.sleep(0.15)  # now comfortably past every reveal time

    server = playback.MatchServer(match, broadcast, port=0, agent_mode="TEST-MODE")
    port = server.httpd.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as r:
                return r.status, r.headers.get("Content-Type", ""), json.loads(r.read().decode("utf-8"))
        code, ct, meta = get("/api/meta")
        assert code == 200 and meta["agent_mode"] == "TEST-MODE" and "utf-8" in ct
        code, ct, tl = get("/api/timeline")
        assert code == 200 and tl["total_entries"] == 13 and tl["visible_entries"] == 13
        assert set(tl["entries"][0]) >= {"minute", "clock", "category", "weight", "commentary",
                                          "reasoning", "tactical_meaning", "watch_for", "t"}
        code, ct, st = get("/api/state")
        assert code == 200 and st["state"]["minute"] >= 0 and "momentum" in st["state"]
        # the root serves the HTML UI, not JSON
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            assert r.status == 200 and "text/html" in r.headers.get("Content-Type", "")
            assert b"Live Match Tactics Agent" in r.read()
        try:
            urllib.request.urlopen(base + "/api/nope", timeout=5)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as err:
            assert err.code == 404
        _t.sleep(0.6)  # now >= finish_t (0.6s) → the replay full-time flip
        code, ct, tl = get("/api/timeline")
        assert tl["finished"] is True
    finally:
        server.stop()
    # leave the committed cache canonical (fresh, pace 1.5, untampered stamps)
    playback.build_replay_cache(match, playback.parse_args(["--mock", "--pace", "1.5"]))
    print("ok  http surface (/api/meta, /api/timeline gating, /api/state, /, 404, full-time flip)")


if __name__ == "__main__":
    test_extract_json()
    test_state()
    test_reasoner_nominations()
    test_verdict_hardening()
    test_narrator_fallback()
    test_end_to_end_instant()
    test_server_surface()
    print("\nALL SMOKE TESTS PASS")
