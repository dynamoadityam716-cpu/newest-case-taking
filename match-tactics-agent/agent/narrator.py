"""Agent 3 — Narrator.

Converts the Tactical Reasoner's verdict into 1–2 sentences of consistent
"scout voice" commentary (brief §4/§6: natural narration, consistent voice).
LLM when a key exists; deterministic template fallback otherwise, so the
keyless pipeline still produces a complete timeline.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are a football scout writing live tactical commentary. \
You receive the Reasoner's verdict on one moment, plus the running state. \
Write the timeline entry: 1–2 sentences, max ~45 words. Voice: precise, \
measured, professional — never hype, never fan-speak, never emoji. Lead with \
the tactical point, not the scoreline. If the verdict is not significant, \
output "" (empty string). Return ONLY JSON: {"commentary": "..."} """

_TEMPLATES = {
    "goal": "GOAL {team} — {player}. {meaning}",
    "penalty": "PENALTY, {team}. {meaning}",
    "red_card": "RED CARD ({team}) — {player} off. {meaning}",
    "substitution": "Substitution, {team}: {player_in} replaces {player_off}. {meaning}",
    "formation": "{team} reshape → {formation}. {meaning}",
    "big_chance": "Big chance for {player} ({team}) — {outcome}. {meaning}",
    "momentum_swing": "Momentum swings to {team}. {meaning}",
}


def _fallback_line(verdict: dict, event: dict, snapshot: dict) -> str:
    """Deterministic scout-voice line from the verdict itself."""
    if not verdict.get("significant"):
        return ""
    cat = verdict.get("category", "noise")
    template = _TEMPLATES.get(cat)
    if not template:
        return verdict.get("reasoning", "").strip()
    meaning = verdict.get("tactical_meaning", "").strip()
    line = template.format(
        minute=event.get("minute", "?"),
        team=event.get("team", "?"),
        player=event.get("player") or verdict.get("reasoning", "")[:40],
        player_in=event.get("player_in", "?"),
        player_off=event.get("player_off", "?"),
        formation=event.get("formation", "?"),
        outcome=event.get("outcome", "?").lower(),
        meaning=meaning,
    )
    # keep it to two sentences max
    parts = [p for p in line.split(". ") if p]
    return ". ".join(parts[:2]).rstrip(".") + ("." if not line.endswith(".") else "")


class Narrator:
    def __init__(self, client=None):
        self.client = client
        self.stats = {"llm_calls": 0, "fallback": 0, "empty": 0}

    def narrate(self, verdict: dict, event: dict, snapshot: dict) -> str:
        if not verdict.get("significant"):
            self.stats["empty"] += 1
            return ""
        if self.client is None:
            self.stats["fallback"] += 1
            return _fallback_line(verdict, event, snapshot)

        self.stats["llm_calls"] += 1
        user = (
            "VERDICT:\n" + _json(verdict)
            + "\n\nCANDIDATE EVENT:\n" + _json(event)
            + "\n\nSTATE:\n" + _json(snapshot)
            + "\n\nReturn the JSON commentary object only."
        )
        try:
            out = self.client.complete_json(SYSTEM_PROMPT, user)
            text = str(out.get("commentary", "")).strip()
            return text
        except Exception:  # noqa: BLE001 — narration must never break playback
            self.stats["fallback"] += 1
            return _fallback_line(verdict, event, snapshot)


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)
