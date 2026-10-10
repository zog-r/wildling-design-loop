---
type: llm
weight: 1
---

This prompt ("Build me a landing page for my productivity app.") gives broad, vague design
direction with no existing target file, mood, or specifics — exactly the case the
`ambition_gate.py` hook is meant to catch (see `hooks/ambition_gate.py`, `classify()` returns
"broad" for this exact sentence).

Pass if, before taking any other action, the agent calls AskUserQuestion with a question that
asks whether to make the request more ambitious (wording may vary slightly, but must convey:
"do you want a bolder/more ambitious version of this request?").

Fail if the agent starts designing, asks unrelated onboarding questions (purpose/audience/mood)
first, or never surfaces an ambition question at all.
