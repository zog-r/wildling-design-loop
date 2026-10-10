#!/usr/bin/env python3
"""UserPromptSubmit hook: broad design direction -> ask "Want me to make this request more ambitious?".

Specific edits ("remove the footer", "make the logo bigger") never fire. `ambition_gate.py selftest` runs the
labeled cases.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

QUESTION = "Want me to make this request more ambitious?"
GUIDE = Path(__file__).resolve().parent.parent / "skills" / "design-loop" / "references" / "ambition.md"

EDIT_VERB = re.compile(
    r"^(?:(?:please|pls|ok|okay|now|also|and|then|can you|could you|would you|let'?s)[\s,]+)*"
    r"(?:remove|delete|hide|move|swap|replace|rename|fix|align|center|centre|shrink|enlarge|increase|decrease|"
    r"bump|nudge|tweak|change|set|update|add|resize|round|darken|lighten|reduce|trim|drop|put|undo|revert|"
    r"use|switch|tighten|loosen|pad|indent|bold|italicize|underline|capitalize|lowercase|uppercase)\b", re.I)
SIZE_EDIT = re.compile(r"\bmake (?:the |this |that |it |them |those |these )?[\w -]{0,30}?\b(?:bigger|smaller|"
                       r"larger|wider|narrower|taller|shorter|bolder|thinner|thicker|lighter|darker|brighter|"
                       r"rounder|less|more \w+er)\b", re.I)
CSS_VALUE = re.compile(r"\b\d+(?:\.\d+)?(?:px|rem|em|pt|vh|vw|ms|s)\b|\b\d+%|#[0-9a-f]{3,8}\b|\brgba?\(|\bhsla?\(",
                       re.I)
POINTER = re.compile(r"\b(?:this|that|the)\s+(?:\w+\s+)?(?:button|link|icon|logo|header|footer|nav|navbar|"
                     r"heading|title|subtitle|label|card|image|photo|border|shadow|margin|padding|gap|line|"
                     r"word|sentence|paragraph|font|color|colour|cta|badge|input|field|column|row|div|span|"
                     r"spacing|text)\b", re.I)
WHOLE = re.compile(r"\b(?:whole|entire|overall|complete(?:ly)?|from scratch|top to bottom|end to end)\b", re.I)
QUESTION_START = re.compile(r"^(?:what|why|where|when|who|which|how (?:does|do|did|is|are|much|many)|is|are|"
                            r"does|did|explain|summari[sz]e|tell me)\b", re.I)
DESIGN_NOUN = re.compile(r"\b(?:landing ?page|home ?page|web ?site|site|page|app|ui|interface|dashboard|screen|"
                         r"design|layout|look|aesthetic|theme|vibe|brand|branding|style|visuals?|hero|portfolio|"
                         r"storefront|onboarding)\b", re.I)
INTENT = re.compile(r"\b(?:build|design|redesign|create|rethink|overhaul|reimagine|revamp|restyle|refresh|"
                    r"mock ?up|prototype|make me|make us|give (?:it|this|the \w+)|"
                    r"change (?:the )?(?:whole|entire|overall)|"
                    r"make (?:it|this|the \w+) (?:feel|look)|(?:want|need) (?:a|an|something))\b", re.I)


def classify(prompt: str) -> str:
    p = " ".join(prompt.split())
    if "[ambitious]" in p.lower():
        return "none"
    if p.startswith("/"):
        return "broad" if re.match(r"/(?:wildling-design:)?design-loop\s+\S", p) else "none"
    if QUESTION_START.match(p):
        return "none"
    if CSS_VALUE.search(p) or SIZE_EDIT.search(p):
        return "specific"
    if (EDIT_VERB.match(p) or POINTER.search(p)) and not WHOLE.search(p):
        return "specific"
    if len(p.split()) >= 4 and DESIGN_NOUN.search(p) and INTENT.search(p):
        return "broad"
    return "none"


def context() -> str:
    return (f"[wildling-design ambition gate] This prompt gives broad design direction. Before any other "
            f"action, call AskUserQuestion with exactly this question: \"{QUESTION}\" and two options: "
            f"\"Yes, make it ambitious\" and \"No, use it as-is\". On yes, read {GUIDE} and follow it to "
            f"transform the prompt, then work from the transformed prompt. On no, proceed with the original.")


CASES = {
    "broad": [
        "Build me a landing page for my productivity app.",
        "design a homepage for my bakery",
        "Can you redesign the dashboard so it feels premium?",
        "I want a website for my photography portfolio",
        "make it feel more like a luxury brand, overhaul the whole look",
        "Create an onboarding screen for the fitness app",
        "reimagine the aesthetic of our pricing page",
        "/design-loop index.html",
        "change the entire vibe of the site to something cozy",
        "give the app a bold new visual style",
    ],
    "specific": [
        "remove the footer",
        "make the logo bigger",
        "change the font to Inter",
        "Please move the CTA button above the fold",
        "set the hero padding to 48px",
        "use #1a1a1a for the background",
        "can you delete the testimonials section",
        "make this card a little smaller on mobile",
        "fix the nav alignment on the landing page",
        "the header text is too long, tighten it",
        "swap the two images in the hero",
    ],
    "none": [
        "what does loop.py do",
        "run the tests",
        "How does the critic score a page?",
        "commit this",
        "/commit",
        "[ambitious] Build me a landing page styled like a pixel-art video game",
        "summarize the email from Lenny",
        "refactor the parser into two functions",
    ],
}


def selftest() -> None:
    bad = [(want, p, classify(p)) for want, ps in CASES.items() for p in ps if classify(p) != want]
    assert not bad, "\n".join(f"want {w}, got {g}: {p}" for w, p, g in bad)
    assert sum(len(v) for v in CASES.values()) >= 20
    print("ambition_gate selftest: OK")


def main() -> int:
    if sys.argv[1:] == ["selftest"]:
        selftest()
        return 0
    try:
        prompt = json.load(sys.stdin).get("prompt") or ""
    except json.JSONDecodeError:
        return 0
    if classify(prompt) == "broad":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": context()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
