"""Mock reasoner + narrator — deterministic, rule-based "verdicts".

Purpose: the whole pipeline (state → reasoner → narrator → timeline) must run
and demo with zero API keys — development, CI-ish checks, and rehearsal when
the venue Wi-Fi eats your key quota. The shape mirrors the LLM verdicts
exactly, so swapping providers changes nothing downstream.

This is explicitly NOT the agentic part — it is the fallback, and it is
disclosed as such in the README.
"""

from __future__ import annotations


def mock_verdict(event: dict, snapshot: dict) -> dict:
    """Same schema the real reasoner returns; confidence capped at 0.55."""
    etype = event["type"]
    team = event.get("team", "?")
    minute = event.get("minute", 0)
    score = snapshot["score"]
    m = snapshot.get("momentum", 0.0)
    momentum_team = snapshot.get("momentum_reading", "even")

    if etype == "Shot" and event.get("outcome") == "Goal":
        xg = event.get("xg")
        xg_note = f" It arrived from a chance worth just xG {xg} — finish overcame the opportunity." if (xg is not None and xg < 0.15) else ""
        return {
            "significant": True, "score": 9, "category": "goal",
            "reasoning": (
                f"Goal for {team} in the {minute}' — it's now {snapshot.get('match', '?')}. "
                f"Game state resets for both benches{momentum_note(m)}"
            ) + xg_note,
            "tactical_meaning": "Forces the trailing side out of its structure; expect immediate chase mode.",
            "watch_for": "Next 5 minutes: does the conceding team press higher or retreat?",
        }
    if etype == "Substitution":
        chasing = _is_chasing(team, score)
        return {
            "significant": True, "score": 6, "category": "substitution",
            "reasoning": (
                f"{team} change at {minute}': {event.get('player_off','?')} makes way for {event.get('player_in','?')}. "
                f"{'They are trailing, so this reads as a push for the game' if chasing else 'Score-level or ahead — likely freshness or a specific matchup fix'}."
            ),
            "tactical_meaning": "Bench decision revealing the coach's read of the game state.",
            "watch_for": "Formation or press intensity shift within 10 minutes of the change.",
        }
    if etype == "Tactical Shift":
        old = snapshot["formations"].get(team, "?")
        return {
            "significant": True, "score": 7, "category": "formation",
            "reasoning": (
                f"{team} reshapes from {old} to {event.get('formation','?')} at {minute}'. "
                f"Formations rarely change without a reason — look at what just happened."
            ),
            "tactical_meaning": "Coach actively restructuring to attack a perceived weakness or protect a lead.",
            "watch_for": "Which zone gains numbers next; full-back positions are the tell.",
        }
    if event.get("card") in ("Red Card", "Second Yellow"):
        return {
            "significant": True, "score": 9, "category": "red_card",
            "reasoning": f"{team} down to ten at {minute}' ({event.get('player','?')}). Structural crisis: someone must sacrifice their role.",
            "tactical_meaning": "Numerical disadvantage forces a block deeper and wider gaps in transition.",
            "watch_for": "Does the ten drop into a mid-block or keep the high line?",
        }
    xg = event.get("xg")
    if etype == "Shot" and xg is not None and xg >= 0.25:
        return {
            "significant": True, "score": 6, "category": "big_chance",
            "reasoning": f"Huge chance for {team} at {minute}' (xG {xg}) — {event.get('outcome','?')}. The chance creation pattern matters more than the miss.",
            "tactical_meaning": "Signals a repeating supply route the defence has not solved yet.",
            "watch_for": "Same combination again inside 10 minutes?",
        }
    if etype == "Shot":
        return {
            "significant": False, "score": 2, "category": "shot",
            "reasoning": f"Routine {event.get('outcome','attempt')} for {team} at {minute}' (xG {xg if xg is not None else 'low'}).",
            "tactical_meaning": "", "watch_for": "",
        }
    # Penalty trigger nomination, fouls, anything else — usually not notable
    return {
        "significant": False, "score": 1, "category": "noise",
        "reasoning": f"Live moment for {team} at {minute}', but nothing structurally new.",
        "tactical_meaning": "", "watch_for": "",
    }


def _is_chasing(team: str, score: dict) -> bool:
    others = [v for k, v in score.items() if k != team]
    return bool(others) and score.get(team, 0) < max(others)


def momentum_note(m: float) -> str:
    if abs(m) < 0.15:
        return " with momentum even."
    return f" and the last five minutes ran {abs(m):.0%} to one side."
