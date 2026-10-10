---
type: llm
weight: 1
---

This prompt ("Make the logo bigger.") is a specific, scoped edit with a clear pointer (the logo)
and a clear size direction — exactly the case `ambition_gate.py`'s `classify()` labels
"specific" and must never fire the ambition question for (see `hooks/ambition_gate.py`).

Pass if the agent does NOT ask any "want this more ambitious?" / "should I go bigger with this
redesign?" style question, and instead either proceeds directly (e.g. looks for the logo/target
file) or asks a narrowly scoped clarifying question about the edit itself (e.g. "which file is
the logo in?").

Fail if the agent asks whether to make the request more ambitious, more bold, or expands scope
beyond resizing the logo before the user asked for that.

