"""Live Match Tactics Agent — the three-agent pipeline.

  state.py     Agent 1  State Tracker      running match state
  reasoner.py  Agent 2  Tactical Reasoner  significance + reasoning (LLM owns the call)
  narrator.py  Agent 3  Narrator           scout-voice commentary
  llm.py       provider-agnostic client (Gemini default / Claude / none)
  mock.py      deterministic keyless fallback verdicts
"""
