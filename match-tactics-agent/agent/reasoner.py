"""Agent 2 — Tactical Reasoner.

The agentic core. Hard separation of duties:

  * RULES only *nominate* candidate moments (bounded cost/latency: ~1 LLM call
    per candidate, not per event — 3.6k events stay affordable).
  * The LLM **owns the significance decision**. Nominated ≠ commented: the
    model rejects routine shots every match, which is exactly the autonomy the
    brief demands (§1: "the agent decides what matters", §4 "genuine plan →
    act → reason loop, not a single-pass prompt").
  * Before every verdict the reasoner **pulls live state from Agent 1**
    (MatchState.snapshot()) — judgment is grounded in running state, not in
    the single event.

Structured JSON verdict for every nomination:
  {significant, score, category, reasoning, tactical_meaning, watch_for}
"""

from __future__ import annotations

import json

from .llm import _extract_json  # tolerant JSON extraction (fences/prose)
from .state import MatchState

SYSTEM_PROMPT = """You are the Tactical Reasoner inside a live match analysis system \
used by professional-style scouts. You receive the running match state and one \
candidate event. Decide, autonomously, whether this moment is TACTICALLY \
SIGNIFICANT — did it change, or credibly threaten to change, how the game is \
being played or will be played?

Significant (typically): goals, penalties won/conceded, red cards, tactical \
substitutions (response to game state), formation/system changes, sustained \
momentum flips. NOT significant: routine shots with low xG, standard fouls, \
half-chances, atmosphere.

You must ground every judgment in the provided state: the score, the clock, \
the formations on the pitch, the substitution and card history, and the \
momentum reading. If the state does not support a tactical story, rule the \
moment not significant even if it looks flashy.

Return ONLY a JSON object:
{
  "significant": true|false,
  "score": 1-10,                  // tactical weight
  "category": "goal|penalty|red_card|substitution|formation|big_chance|momentum_swing|noise",
  "reasoning": "2-4 sentences: WHY this matters NOW, citing the state you were given",
  "tactical_meaning": "what it changes about how the game is played (1-2 sentences, '' if not significant)",
  "watch_for": "one concrete thing to watch next (max 20 words, '' if not significant)"
}"""


class Reasoner:
    def __init__(self, client=None, mock=None, max_window_events: int = 14):
        """client: agent.llm.LLMClient (real path). mock: callable(event,
        snapshot) -> dict — the keyless fallback (agent/mock.mock_verdict)."""
        self.client = client
        self.mock = mock
        self.max_window_events = max_window_events
        self.stats = {"nominated": 0, "llm_calls": 0, "mock_calls": 0, "significant": 0}

    # ------------------------------------------------------------------ #

    def is_candidate(self, event: dict) -> bool:
        """Rule layer: cheap nomination pass. Deliberately generous — the
        LLM owns the real decision."""
        etype = event["type"]
        if etype in ("Substitution", "Tactical Shift"):
            return True
        if event.get("card") in ("Red Card", "Second Yellow"):
            return True
        if etype == "Shot":
            return True  # model filters routine shots using state
        if etype == "Penalty":
            return True
        if etype == "Foul":
            loc = event.get("location") or []
            if not loc:
                return False
            x = loc[0]
            return x <= 25 or x >= 95  # dangerous-zone fouls only
        return False

    # ------------------------------------------------------------------ #

    def judge(self, event: dict, state: MatchState) -> dict:
        """Pull live state from Agent 1, then get a verdict (LLM or mock)."""
        self.stats["nominated"] += 1
        snapshot = state.snapshot()
        snapshot["recent_events"] = snapshot["recent_events"][-self.max_window_events:]

        if self.client is None:
            self.stats["mock_calls"] += 1
            verdict = self.mock(event, snapshot) if self.mock else _no_verdict(event)
        else:
            self.stats["llm_calls"] += 1
            verdict = self._judge_llm(event, snapshot)

        if verdict.get("significant"):
            self.stats["significant"] += 1
        return verdict

    def _judge_llm(self, event: dict, snapshot: dict) -> dict:
        user = (
            "RUNNING MATCH STATE (from the State Tracker agent):\n"
            + json.dumps(snapshot, ensure_ascii=False, indent=1)
            + "\n\nCANDIDATE EVENT:\n"
            + json.dumps(event, ensure_ascii=False)
            + "\n\nDecide significance and return the JSON verdict object only."
        )
        try:
            verdict = self.client.complete_json(SYSTEM_PROMPT, user)
            if not isinstance(verdict, dict):
                # defensive: some provider wrappers hand back raw text —
                # re-parse with the same tolerant extractor rather than crash
                verdict = _extract_json(str(verdict))
        except Exception as err:  # noqa: BLE001 — a model hiccup must not kill playback
            verdict = _no_verdict(event)
            verdict["reasoning"] = f"[reasoner unavailable: {err}] " + verdict["reasoning"]
        # hard schema fix-ups — the pipeline never trusts the model blindly
        verdict.setdefault("significant", False)
        verdict.setdefault("score", 1)
        verdict.setdefault("category", "noise")
        verdict.setdefault("reasoning", "")
        verdict.setdefault("tactical_meaning", "")
        verdict.setdefault("watch_for", "")
        verdict["significant"] = bool(verdict["significant"])
        try:
            verdict["score"] = max(1, min(10, int(verdict["score"])))
        except (TypeError, ValueError):
            verdict["score"] = 1
        return verdict


def _no_verdict(event: dict) -> dict:
    return {
        "significant": False, "score": 1, "category": "noise",
        "reasoning": f"Candidate {event.get('type')} at {event.get('minute')}' could not be judged.",
        "tactical_meaning": "", "watch_for": "",
    }
