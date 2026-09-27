#!/usr/bin/env python3
"""Live Match Tactics Agent — playback entrypoint.

Three modes:

1. CLI print mode (default) — stream the match through the agents and print
   the timeline to the terminal:

       python playback.py --mock                 # keyless fallback agents
       python playback.py --pace 1.5             # demo pace, printed timeline
       python playback.py --start-minute 50 --end-minute 70

2. Live server — the three-agent loop runs in a background thread at scaled
   pace while a stdlib HTTP server reveals the timeline progressively:

       python playback.py --live --pace 1.5 --port 8017

3. Replay server — pre-generate the full timeline once into
   ``cache/<match_id>.json`` and serve it deterministically at the same pace
   (brief §8: stable demo, disclosed, not fabricated):

       python playback.py --replay --pace 1.5    # uses/regenerates the cache

Server surface (all JSON is UTF-8):
   GET /                 → ui/index.html
   GET /ui/<file>        → other static ui assets
   GET /api/state        → visible scoreboard snapshot (score, minute,
                           formations, momentum, progress)
   GET /api/timeline     → entries revealed so far (each with the agent's
                           full verdict: significant, score, reasoning,
                           tactical_meaning, watch_for)

Agents (unchanged from pass 1):
    Agent 1  State Tracker      (agent/state.py)
    Agent 2  Tactical Reasoner  (agent/reasoner.py)
    Agent 3  Narrator           (agent/narrator.py)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.llm import LLMClient, available_provider          # noqa: E402
from agent.mock import mock_verdict                          # noqa: E402
from agent.narrator import Narrator                          # noqa: E402
from agent.reasoner import Reasoner                          # noqa: E402
from agent.state import MatchState, iter_match, total_match_minutes  # noqa: E402

ROOT = Path(__file__).resolve().parent
DEFAULT_DATA = ROOT / "data" / "match.json"
CACHE_DIR = ROOT / "cache"
UI_DIR = ROOT / "ui"
DEFAULT_PORT = 8017

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml"}


def _force_utf8_stdio() -> None:
    """Windows consoles default to cp1252 and crash on non-Latin names."""
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(DEFAULT_DATA), help="cleaned match json (default data/match.json)")
    ap.add_argument("--mock", action="store_true", help="force the rule-based fallback (no LLM calls)")
    ap.add_argument("--llm", action="store_true", help="require an LLM key (fail fast if none)")
    ap.add_argument("--live", action="store_true", help="serve: run the agents live in a background thread")
    ap.add_argument("--replay", action="store_true", help="serve: pre-generate once, replay deterministically")
    ap.add_argument("--regen", action="store_true", help="with --replay: rebuild the cache even if it exists")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="server port (default 8017)")
    ap.add_argument("--pace", type=float, default=0.0, metavar="SEC",
                    help="seconds of wall clock per match-minute; 0 = instant (default 0)")
    ap.add_argument("--start-minute", type=int, default=0)
    ap.add_argument("--end-minute", type=int, default=10_000)
    ap.add_argument("--quiet", action="store_true", help="CLI mode: only print the timeline entries")
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


# --------------------------------------------------------------------------- #
# broadcast — what the HTTP layer is allowed to see, gated by the match clock  #
# --------------------------------------------------------------------------- #

class Broadcast:
    """Thread-safe ledger of what has "happened" so far.

    The producer may run ahead of wall clock (LLM latency is uneven), so every
    timeline entry and every state sample carries a reveal time ``t`` (seconds
    since server start). Handlers only return items whose ``t`` has passed —
    that is what makes the API feel live even when the producer doesn't.
    """

    def __init__(self, meta: dict, t0: float):
        self.lock = threading.Lock()
        self.meta = meta
        self.t0 = t0
        self.entries: list[dict] = []
        self.trail: list[dict] = []      # state samples: [{t, ...compact snapshot}]
        self.finished = False
        self.finish_t: float | None = None   # replay mode: wall-clock full-time

    def publish_state(self, snapshot: dict, t: float) -> None:
        sample = {
            "t": t,
            "minute": snapshot.get("minute", 0),
            "period": snapshot.get("period", 0),
            "score": snapshot.get("score", {}),
            "formations": snapshot.get("formations", {}),
            "momentum": snapshot.get("momentum", 0.0),
            "momentum_reading": snapshot.get("momentum_reading", "even"),
            "events_applied": snapshot.get("events_applied", 0),
            "shot_totals": snapshot.get("shot_totals", {}),
        }
        with self.lock:
            self.trail.append(sample)

    def publish_entry(self, entry: dict, t: float) -> None:
        out = dict(entry)
        out["t"] = t
        with self.lock:
            self.entries.append(out)

    def set_finished(self) -> None:
        with self.lock:
            self.finished = True

    # -- read side (called from HTTP handlers) ------------------------------ #

    def _now(self) -> float:
        return time.monotonic() - self.t0

    def visible_state(self) -> dict:
        with self.lock:
            now = self._now()
            sample = None
            for s in self.trail:            # last sample whose moment passed
                if s["t"] <= now:
                    sample = s
            return dict(sample) if sample else {"t": 0.0}

    def visible_entries(self) -> list[dict]:
        with self.lock:
            now = self._now()
            return [e for e in self.entries if e["t"] <= now]

    def counts(self) -> tuple[int, int]:
        with self.lock:
            return len(self.entries), sum(1 for e in self.entries if e["t"] <= self._now())

    def is_finished(self) -> bool:
        with self.lock:
            if self.finished:
                return True
            return self.finish_t is not None and self._now() >= self.finish_t


def _compact_snapshot(state: MatchState) -> dict:
    snap = state.snapshot()
    snap["events_applied"] = state.events_applied()
    return snap


def run(match: dict, reasoner: Reasoner, narrator: Narrator, args, broadcast: Broadcast | None = None):
    """Stream the match through the agents.

    With ``broadcast`` (server modes) every significant entry and periodic
    state samples are published with reveal times; the full CLI printing
    behavior is kept when it is None (used by the smoke tests).
    Returns the timeline list (all entries, regardless of reveal gating).
    """
    state = MatchState(match)
    timeline: list[dict] = []
    pace = args.pace
    clock_prev = 0.0
    t0 = time.monotonic()
    last_sampled_minute = -1

    if broadcast:
        broadcast.publish_state(_compact_snapshot(state), 0.0)

    if not args.quiet and not broadcast:
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
            if broadcast and int(clock) != last_sampled_minute:
                broadcast.publish_state(_compact_snapshot(state), time.monotonic() - t0)
                last_sampled_minute = int(clock)
            continue

        verdict = reasoner.judge(event, state)
        if broadcast:
            broadcast.publish_state(_compact_snapshot(state), time.monotonic() - t0)
            last_sampled_minute = int(clock)
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

        if broadcast:
            broadcast.publish_entry(entry, time.monotonic() - t0)

        marker = {"goal": "⚽", "red_card": "🟥", "penalty": "❗", "formation": "⟐",
                  "substitution": "⇄", "big_chance": "△", "momentum_swing": "≈"}.get(entry["category"], "•")
        if not args.quiet:
            print(f"\n{entry['clock']:>4} [{entry['score']}] {marker} {entry['team']}: {line}")
            if entry.get("reasoning"):
                print(f"      └─ why: {entry['reasoning']}")
            if entry.get("watch_for"):
                print(f"         watch: {entry['watch_for']}")

    if broadcast:
        broadcast.publish_state(_compact_snapshot(state), time.monotonic() - t0)
        broadcast.set_finished()
    elif not args.quiet:
        print(f"\n=== FULL TIME {state.score[state.home]}-{state.score[state.away]} "
              f"({match['home_team']} {state.score[state.home]} - {state.score[state.away]} {match['away_team']}) ===")
        print(f"events applied: {state.events_applied()}")
        print(f"reasoner: {reasoner.stats}")
        print(f"narrator: {narrator.stats}")
        print(f"timeline entries: {len(timeline)}  |  wall time: {time.monotonic() - t0:.1f}s")
    return timeline


# --------------------------------------------------------------------------- #
# replay cache — pre-generated timeline, deterministic demo                     #
# --------------------------------------------------------------------------- #

def cache_path_for(match: dict) -> Path:
    return CACHE_DIR / f"{match.get('match_id', 'match')}.json"


def build_replay_cache(match: dict, args) -> dict:
    """Run the full pipeline instantly (with a Broadcast to capture the real
    state trail), then re-stamp reveal times deterministically from the match
    clock: t = minute * pace (+ small offsets so state leads entries)."""
    t0 = time.monotonic()
    reasoner, narrator, mode = build_agents(args)
    instant = argparse.Namespace(**{**vars(args), "pace": 0.0, "quiet": True})
    b = Broadcast({}, t0=time.monotonic())
    timeline = run(match, reasoner, narrator, instant, broadcast=b)
    pace = args.pace
    for entry in timeline:
        entry["t"] = round(entry["minute"] * pace + 1.0, 3)
    trail: dict[int, dict] = {}
    for sample in b.trail:
        sample["t"] = round(sample["minute"] * pace + 0.25, 3)
        trail[sample["minute"]] = sample  # one sample per match-minute (last wins)
    state_trail = [trail[m] for m in sorted(trail)]
    payload = {
        "mode": mode,
        "pace": pace,
        "generated_from": Path(args.data).name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wall_seconds": round(time.monotonic() - t0, 1),
        "match": _match_meta(match),
        "state_trail": state_trail,
        "timeline": timeline,
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path_for(match)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"replay cache written: {path.relative_to(ROOT)} "
          f"({len(timeline)} entries, {len(state_trail)} state samples, "
          f"generated in {payload['wall_seconds']}s, mode: {mode})")
    return payload


def load_replay_cache(match: dict, args) -> dict:
    path = cache_path_for(match)
    if path.exists() and not args.regen:
        payload = json.loads(path.read_text(encoding="utf-8"))
        print(f"replay cache loaded: {path.relative_to(ROOT)} "
              f"({len(payload['timeline'])} entries, mode: {payload['mode']})")
        return payload
    return build_replay_cache(match, args)


# --------------------------------------------------------------------------- #
# the server                                                                   #
# --------------------------------------------------------------------------- #

class MatchServer:
    """Serves the UI + JSON API over stdlib http.server, UTF-8 everywhere."""

    def __init__(self, match: dict, broadcast: Broadcast, port: int, agent_mode: str = ""):
        self.broadcast = broadcast
        self.match_meta = _match_meta(match)
        self.agent_mode = agent_mode
        handler = type("Handler", (_Handler,), {"server_ref": self})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True

    def serve_forever(self):
        self.httpd.serve_forever()

    def stop(self):
        self.httpd.shutdown()


class _Handler(BaseHTTPRequestHandler):
    server_ref: MatchServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quieter logs, still informative
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("http %s\n" % (fmt % args))

    # -- helpers ------------------------------------------------------------ #

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(path.suffix.lower(), "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ------------------------------------------------------------- #

    def do_GET(self):  # noqa: N802 (stdlib naming)
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            self._send_file(UI_DIR / "index.html")
        elif route.startswith("/ui/"):
            target = (UI_DIR / route[len("/ui/"):]).resolve()
            if UI_DIR.resolve() not in target.parents or target == UI_DIR.resolve():
                self._send_json({"error": "forbidden"}, 403)
            else:
                self._send_file(target)
        elif route == "/api/state":
            b = self.server_ref.broadcast
            self._send_json({
                "match": b.meta,
                "state": b.visible_state(),
                "finished": b.is_finished(),
            })
        elif route == "/api/timeline":
            b = self.server_ref.broadcast
            total, visible = b.counts()
            self._send_json({
                "match": b.meta,
                "total_entries": total,
                "visible_entries": visible,
                "finished": b.is_finished(),
                "entries": b.visible_entries(),
            })
        elif route == "/api/meta":
            self._send_json({"agent_mode": self.server_ref.agent_mode})
        else:
            self._send_json({"error": "not found"}, 404)


def serve(match: dict, args, broadcast: Broadcast, agent_mode: str = "", max_seconds: int | None = None) -> None:
    server = MatchServer(match, broadcast, args.port, agent_mode)
    url = f"http://127.0.0.1:{args.port}"
    print(f"serving: {url}   (mode: {'live' if args.live else 'replay'}, pace: {args.pace}s/min)")
    print(f"  ui:      {url}/")
    print(f"  state:   {url}/api/state")
    print(f"  timeline:{url}/api/timeline")
    try:
        if max_seconds:
            # testability knob: MTA_SERVE_SECONDS auto-stops the server (demo
            # scripting / CI probes); unset means serve until Ctrl-C.
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            time.sleep(max_seconds)
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        print("server stopped")


def _producer_live(match: dict, args, broadcast: Broadcast, reasoner: Reasoner, narrator: Narrator) -> None:
    print("live producer starting")
    run(match, reasoner, narrator, args, broadcast=broadcast)


def _match_meta(match: dict) -> dict:
    return {k: match.get(k) for k in ("match_id", "competition", "season", "match_date",
                                      "home_team", "away_team", "home_score", "away_score")}


def _broadcast_from_cache(match: dict, payload: dict, args) -> Broadcast:
    """Replay: seed the ledger from the pre-generated cache.

    The cache stores authoritative reveal stamps (t) at generation pace. If
    the serve-time --pace matches the cache pace, those stamps are honored
    verbatim; if the pace changed, stamps are recomputed from the match
    minutes so the demo cadence stays adjustable without regenerating."""
    b = Broadcast(_match_meta(match), t0=time.monotonic())
    pace = args.pace
    cache_pace = payload.get("pace", pace)
    same_pace = abs(float(cache_pace) - float(pace)) < 1e-9
    for sample in payload.get("state_trail", []):
        s = dict(sample)
        s["t"] = float(s["t"]) if (same_pace and s.get("t") is not None) \
            else round(s["minute"] * pace + 0.25, 3)
        b.trail.append(s)
    for entry in payload["timeline"]:
        e = dict(entry)
        e["t"] = float(e["t"]) if (same_pace and e.get("t") is not None) \
            else round(e["minute"] * pace + 1.0, 3)
        b.entries.append(e)
    if b.entries:
        b.finish_t = max(e["t"] for e in b.entries) + max(pace, 1.0)
    return b


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

    if args.live or args.replay:
        if args.live:
            reasoner, narrator, mode = build_agents(args)
            broadcast = Broadcast(_match_meta(match), t0=time.monotonic())
            threading.Thread(target=_producer_live, args=(match, args, broadcast, reasoner, narrator),
                             daemon=True).start()
        else:
            payload = load_replay_cache(match, args)
            mode = payload.get("mode", "")
            broadcast = _broadcast_from_cache(match, payload, args)
        serve(match, args, broadcast, mode,
              max_seconds=int(os.environ.get("MTA_SERVE_SECONDS") or 0) or None)
        return 0

    reasoner, narrator, mode = build_agents(args)
    if not args.quiet:
        print(f"agent mode: {mode}")
    timeline = run(match, reasoner, narrator, args)
    out = CACHE_DIR / "timeline_last_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"mode": mode, "timeline": timeline}, ensure_ascii=False, indent=1), encoding="utf-8")
    if not args.quiet:
        print(f"saved: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
