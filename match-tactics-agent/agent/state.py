"""Agent 1 — State Tracker.

Maintains the running match state as the event stream is applied, event by
event: score, minute/period, both teams' current formations, substitutions,
cards, a rolling momentum estimate, and a compact window of recent events.

The Tactical Reasoner (agent 2) pulls this state before reasoning about any
candidate moment — the agent is *required* to ground its judgment here, which
is what makes the loop agentic rather than a single-pass prompt (brief §4).

Pure stdlib. Knows nothing about LLMs.
"""

from __future__ import annotations

import math
from typing import Any

# Events the Reasoner is offered as candidate moments (brief: subs, formation
# changes, goals, red cards, penalties, big momentum swings). Everything else
# is context, not a trigger.
TRIGGER_TYPES = ("Shot", "Substitution", "Tactical Shift", "Bad Behaviour", "Penalty", "Foul")

RED_CARD_LIKE = ("Red Card", "Second Yellow")

# momentum window / weighting
MOMENTUM_WINDOW_MIN = 5.0
_MOM_EVENT_WEIGHT = {
    "Shot": 3.0,
    "Pass": 0.35,
    "Carry": 0.2,
    "Pressure": 0.6,
    "Dribble": 0.8,
}


def _event_clock(e: dict) -> float:
    return float(e.get("minute", 0)) + float(e.get("second", 0)) / 60.0


def _x(e: dict) -> float | None:
    loc = e.get("location")
    return float(loc[0]) if loc else None


def _final_third(team_direction: int, x: float) -> bool:
    return (x >= 80.0) if team_direction > 0 else (x <= 20.0)


class MatchState:
    """Running state for one match. apply(e) advances it; snapshot() is what
    the Reasoner sees."""

    def __init__(self, meta: dict):
        self.meta = meta
        self.home = meta["home_team"]
        self.away = meta["away_team"]
        self.score = {self.home: 0, self.away: 0}
        # formations are stored as strings everywhere ("4231", "433")
        self.formations = {
            self.home: str(meta.get("home_formation_start") or "") or None,
            self.away: str(meta.get("away_formation_start") or "") or None,
        }
        self.period = 0
        self.minute = 0
        self.second = 0
        self.cards: list[dict] = []       # applied cards
        self.subs: list[dict] = []        # applied substitutions
        self.shots: list[dict] = []       # every shot (for xG totals)
        self.recent: list[dict] = []      # rolling window (compact dicts)
        self.recent_window = 14           # events kept for context
        self._events: list[dict] = []     # full cleaned stream so far
        self._momentum: list[tuple[float, str, float]] = []  # (clock, team, weight)

    # ------------------------------------------------------------------ #
    # team direction: home attacks +x in odd periods; mirrored in even ones.

    def _direction(self, team: str, period: int) -> int:
        is_home = team == self.home
        base = 1 if is_home else -1
        return base if period % 2 == 1 else -base

    # ------------------------------------------------------------------ #

    def apply(self, e: dict) -> None:
        etype = e["type"]
        team = e.get("team")
        self.period = e.get("period", self.period)
        self.minute = e.get("minute", self.minute)
        self.second = e.get("second", 0)

        # momentum feed (before outcome branches so goals count too)
        weight = _MOM_EVENT_WEIGHT.get(etype, 0.0)
        if weight and team:
            x = _x(e)
            if etype == "Pass" and x is not None:
                # only attacking-zone passes count, keep the feed focused
                if not _final_third(self._direction(team, e.get("period", 1)), x):
                    weight = 0.0
            if weight:
                self._momentum.append((_event_clock(e), team, weight))

        if etype == "Shot":
            self.shots.append({
                "minute": e.get("minute"), "team": team, "player": e.get("player"),
                "outcome": e.get("outcome"), "xg": e.get("xg"),
            })
            if e.get("outcome") == "Goal":
                self.score[team] = self.score.get(team, 0) + 1

        elif etype in ("Starting XI", "Tactical Shift") and e.get("formation"):
            if team in self.formations and etype == "Tactical Shift":
                self.formations[team] = str(e["formation"])

        elif etype == "Substitution":
            self.subs.append({
                "minute": e.get("minute"), "team": team,
                "player_in": e.get("player_in"), "player_off": e.get("player_off"),
                "reason": e.get("sub_reason"),
            })

        elif e.get("card") in RED_CARD_LIKE:
            self.cards.append({
                "minute": e.get("minute"), "team": team, "player": e.get("player"),
                "card": e["card"], "reason": e.get("card_reason"),
            })

        self._events.append(e)
        self.recent.append(self._compact(e))
        if len(self.recent) > self.recent_window:
            self.recent.pop(0)

    # ------------------------------------------------------------------ #

    def momentum(self) -> float:
        """−1 (away rolling them over) … +1 (home on top), last 5 match-min."""
        clock = self.minute + self.second / 60.0
        window = [(t, team, w) for (t, team, w) in self._momentum if clock - t <= MOMENTUM_WINDOW_MIN]
        home_w = sum(w for _, team, w in window if team == self.home)
        away_w = sum(w for _, team, w in window if team == self.away)
        total = home_w + away_w
        if total <= 0:
            return 0.0
        return max(-1.0, min(1.0, (home_w - away_w) / total))

    def shot_totals(self) -> dict:
        def agg(team: str) -> dict:
            mine = [s for s in self.shots if s["team"] == team]
            return {
                "shots": len(mine),
                "on_target": sum(1 for s in mine if s.get("outcome") in ("Saved", "Goal", "Post")),
                "goals": sum(1 for s in mine if s.get("outcome") == "Goal"),
                "xg": round(sum(s["xg"] or 0.0 for s in mine), 2),
            }
        return {self.home: agg(self.home), self.away: agg(self.away)}

    def events_applied(self) -> int:
        return len(self._events)

    # ------------------------------------------------------------------ #

    def _compact(self, e: dict) -> dict:
        """Compact event for the recent-window the LLM sees."""
        out = {"minute": e.get("minute"), "type": e["type"], "team": e.get("team")}
        if e.get("player"):
            out["player"] = e["player"]
        for key in ("outcome", "xg", "formation", "player_in", "player_off", "card"):
            if e.get(key) is not None:
                out[key] = e[key]
        return out

    def snapshot(self) -> dict[str, Any]:
        """Everything Agent 2 needs, as a plain JSON-able dict."""
        return {
            "match": f"{self.home} {self.score[self.home]}-{self.score[self.away]} {self.away}",
            "period": self.period,
            "minute": self.minute,
            "formations": {k: v for k, v in self.formations.items() if v},
            "score": dict(self.score),
            "cards": list(self.cards),
            "recent_subs": self.subs[-3:],
            "shot_totals": self.shot_totals(),
            "momentum": round(self.momentum(), 2),  # -1 away … +1 home
            "momentum_reading": self._momentum_words(),
            "recent_events": list(self.recent),
        }

    def _momentum_words(self) -> str:
        m = self.momentum()
        if abs(m) < 0.15:
            return "even"
        side = self.home if m > 0 else self.away
        strength = "strong" if abs(m) >= 0.6 else "clear"
        return f"{strength} momentum with {side}"


def iter_match(match: dict):
    """Load a cleaned match file and yield events in playback order."""
    yield from sorted(match["events"], key=lambda e: (e.get("p", 0), e.get("minute", 0), e.get("second", 0), e.get("i", 0)))


def total_match_minutes(match: dict) -> float:
    last = max((e.get("minute", 0) + e.get("second", 0) / 60.0) for e in match["events"])
    return math.ceil(last)
