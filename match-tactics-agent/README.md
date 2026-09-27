# Live Match Tactics Agent

An agentic AI system that watches football match event data and autonomously
decides which moments are tactically significant, reasons about why they
happened, and narrates them like a scout — built for the "Live Match Tactics
Agent" hackathon brief. Post-match engine over [StatsBomb open data](
https://github.com/statsbomb/open-data), presented as **simulated live** by
streaming pre-recorded events at scaled real-time pace.

The differentiator from a normal LLM summarizer is autonomy: the agent decides
what matters and explains its reasoning, rather than being told what to
comment on.

## The three agents

```
data/match.json (cleaned StatsBomb events)
        |  playback at scaled pace (1 match-minute = --pace seconds)
        v
Agent 1 - State Tracker      agent/state.py
        |                    score, minute, formations, subs, cards,
        |                    recent-event window, momentum heuristic
        |  trigger nominations: goals, shots, subs, formation changes, red cards
        v
Agent 2 - Tactical Reasoner  agent/reasoner.py
        |                    pulls live state from Agent 1, the MODEL decides
        |                    significance -> structured JSON verdict
        v
Agent 3 - Narrator           agent/narrator.py
        |                    verdict -> 1-2 sentences, consistent scout voice
        v
Timeline   CLI print, or the JSON API + live UI (playback.py --live/--replay)
```

## Why it's agentic (the judges' question)

- **Rules only nominate candidate moments** (~30 per match out of ~3,700
  events) — that bounds cost and latency. Nomination is *not* the decision.
- **The Reasoner model owns the significance call.** It routinely rejects
  routine low-xG shots that the rules pass it, and upgrades moments the rules
  under-weight, grounding every verdict in state it *pulls from Agent 1* at
  judgment time: score, clock, formations, sub/card history, momentum.
- That makes it a genuine **plan → act → reason loop**, not a single-pass
  prompt: the model reasons *over* structured tactical events (not over
  commentary text — this is not sentiment analysis).

## Run it

Python 3.12+ standard library only — no `pip install` required.

```bash
# 1) data: list a season, find a match id, fetch + clean it
python fetch_data.py --list  --matches-file 11/2        # La Liga 2016/17
python fetch_data.py --search --match 266033 --competition 11
python fetch_data.py --match 266033 --matches-file 11/2  # -> data/match.json

# 2) terminal demo: the whole pipeline, keyless (rule-based fallback agents)
python playback.py --mock
python playback.py --mock --pace 1.5                    # "live" pacing
python playback.py --mock --start-minute 48 --end-minute 62

# 3) with a real model
export GEMINI_API_KEY=...        # default provider (free key: aistudio.google.com/apikey)
python playback.py --pace 1.5    # auto-uses Gemini when a key is present
export ANTHROPIC_API_KEY=...     # or Claude; LLM_PROVIDER=claude forces it
python playback.py --llm         # fail fast if no key is configured

#    ...or skip env vars entirely: put the key in a git-ignored `.env`
#    (copy `.env.example` to the repo root or this folder):
#      GEMINI_API_KEY=AIza...
#    Existing environment variables always win over the file.

# 4) served demo with the browser UI
python playback.py --replay --pace 1.5 --port 8017   # deterministic (pre-generated)
python playback.py --live   --pace 1.5 --port 8017   # agents run while you watch
#   open http://127.0.0.1:8017/            scoreboard + live timeline UI
#   GET /api/state      visible scoreboard snapshot (score, minute, formations, momentum)
#   GET /api/timeline   entries revealed so far, each with the full agent verdict
#   GET /api/meta       which agent mode is running

# tests (no network, no keys)
python tests/test_smoke.py
```

The committed demo data is **Valencia 2-3 Barcelona, 2016-10-22** (id 266033)
— Barça lead early, trail 2-1 from 55', equalize, and Messi wins it in the
90th; Valencia reshapes in-match at 45' and 77'. The pre-generated replay
cache ships as `cache/266033.json` so the deterministic demo needs zero
network and zero keys.

> Data-source note (2026): the StatsBomb open-data repository now lives at
> `github.com/hudl/open-data`; the legacy `statsbomb/open-data` raw URLs 404
> for most files. `fetch_data.py` pins to the current layout.

## Demo script (matches the brief)

1. Start the replay server:
   `python playback.py --replay --pace 1.5 --port 8017`, open
   `http://127.0.0.1:8017/`. The timeline scrolls by itself; entries appear as
   the match clock advances.
2. Let it run to ~minute 51-61 (Valencia's two goals, then the response sub).
   Watch the scoreboard and momentum needle move before the commentary does.
3. **Pause at the tactical moment** (the 45'/77' Valencia formation shifts, or
   Barcelona's 61' equalizer) and expand an entry: the panel shows the agent's
   full verdict — significance weight, *why now* reasoning grounded in the
   running state, tactical meaning, and what to watch next. That reasoning is
   the product, not just the commentary line.
4. Close on the key line: **this isn't sentiment analysis on commentary text —
   it's reasoning over structured tactical events, deciding what matters and
   why, autonomously.**

## Disclosure (demo integrity)

**Replay mode (`--replay`) presents pre-generated outputs**: the timeline was
produced by the same three-agent pipeline ahead of time and is replayed at
paced reveal times for demo stability (brief §8). It is not fabricated
content and not a live API — the cache records the generating mode, and
`--live` runs the agents in real time if you want to show the loop itself.
When run with a real LLM key, verdicts come from the model; the committed
cache was generated in keyless mock mode (rule-based fallback), and any
keyless run also uses that fallback. Say which mode you're demoing.

## LangGraph swap-in note

The orchestration is a deliberate plain-Python loop (`playback.py` calls the
three agent modules in sequence) — the brief allows this under time pressure
and it keeps the demo dependency-free. The modules are already shaped like a
graph: each agent is a node taking (state, event) and emitting downward.
To swap in LangGraph: define `StateTracker`, `Reasoner`, `Narrator` nodes,
pass the match dict + running state through a `TypedDict` state, keep the
trigger nomination as a conditional edge into the Reasoner node, and mount the
Broadcast publisher inside the Narrator node. No agent code needs to change —
only the loop that drives them.

## Project layout

```
fetch_data.py          list / search / fetch / clean StatsBomb open data
data/match.json        committed cleaned demo match (0.6 MB, no network needed)
agent/state.py         Agent 1 - State Tracker (MatchState)
agent/reasoner.py      Agent 2 - Tactical Reasoner (nomination + LLM verdict)
agent/narrator.py      Agent 3 - Narrator (scout-voice commentary)
agent/llm.py           Gemini (default) / Claude stdlib client, retries, JSON mode
agent/mock.py          keyless rule-based fallback verdicts
playback.py            CLI playback + --live/--replay servers + Broadcast ledger
ui/index.html          dark scoreboard + live tactical timeline (vanilla JS)
cache/266033.json      pre-generated replay timeline for the demo match
tests/test_smoke.py    dependency-free smoke tests (7 groups)
```

## Verification status (honest)

- `python tests/test_smoke.py` → **7/7 pass**: JSON extraction, full-match
  state tracking, nomination purity, Reasoner hardening against five stub LLM
  clients (clean / prose-wrapped / exploding / lazy-typed / garbage), narrator
  fallback, instant end-to-end run, and the HTTP surface (gating, cache
  round-trip, full-time flip, 404).
- **Mock and replay paths are verified end-to-end** through the real server
  (progressive reveal probed early/mid/late in both serve modes; UI exercised
  in a browser: 1s polls, clean console, FULL TIME at 13/13, final score 2-3).
- **The live-key LLM path is not yet exercised** — no API key was available
  during the build. The Gemini/Claude clients are covered by stub-client tests
  and the JSON/schema hardening, but real-model verdict quality (and key
  handling against the live API) is unproven. Run `playback.py --pace 1.5`
  with a key before trusting it on stage; `--mock`/`--replay` demos need
  nothing.
