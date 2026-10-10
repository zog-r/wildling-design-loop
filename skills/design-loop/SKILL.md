---
name: design-loop
description: Use this skill when the user wants to take an HTML page (existing or new) to studio-level design quality, says "/design-loop", "make this page look world-class", "redesign this page", "run the design loop", or passes `--brief <path>` for a headless run. Seeds N distinct directions, picks one, then runs full-redesign rounds judged by a blind critic in a fresh `claude -p` process until the score passes, plateaus, or hits the round cap.
---

# design-loop

All logic lives in `scripts/loop.py`. You design pages and call it; it handles state, seeds, screenshots,
critic calls, acceptance, decisions, and `.design-loop/manifest.json`. Run everything from the user's
project dir. `L` below means `python3 <this skill dir>/scripts/loop.py`.

Never pass the critic anything yourself. `loop.py` sends it screenshots only.

## 1. Onboard

- **Headless** (`--brief <path>` given): run `L init --brief <path>`. From here on, never ask questions and
  never stop to check in; run to a stop decision and finish.
- **Interactive** (default): at most 2 AskUserQuestion rounds.
  - Round 1: target file or new page; purpose/audience; mood; must-keep content.
  - Round 2: optional inspiration, moodboard image paths, number of directions (default 3), max rounds
    (default 6), critic model (default fable), pass grade (default 9), polish round (default yes), enrich
    (`images` default, `images+video`, `off`), output file.
  - If the mood/inspiration answer is broad and the request isn't already marked `[ambitious]`, ask once:
    "Want me to make this request more ambitious?" On yes, follow `references/ambition.md` and put the result
    in `inspiration`.
  Write the answers to `.design-loop.brief.json` with keys `target` ("new" or path), `output_file`,
  `purpose`, `audience`, `mood`, `inspiration`, `must_keep`, `constraints`, `moodboard`, `directions`,
  `max_iters`, `critic_model`, `pass_grade`, `polish`, `enrich`, `media_model` (default `claude-opus-5-5`),
  `settle_ms` (default 8000: each fold is shot again after timed motion ends; 0 turns that off).
  Then `L init --brief .design-loop.brief.json`.

Read `.design-loop/brief.json` for the validated brief.

## 2. Discover

For k = 1..directions:
1. `L seed --id dk` prints a 64-char seed. Derive the creative direction from it and commit to it:
   chars 1–8 → color (palette, temperature, contrast), 9–16 → type (families, scale, weight),
   17–32 → layout (grid, composition, density), 33–48 → subpatterns (texture, motif, interaction),
   49–64 → special numbers (column count, angles, rhythm, ratios). Make each direction clearly distinct.
   The seed string must never appear in the page (`loop.py` rejects it).
2. Build the full page to `.design-loop/variants/dk.html` from the brief (existing target: redesign it and
   keep its content). Keep every must-keep number and date verbatim. Honor `constraints`.
3. `L shot dk`.

Pick:
- Interactive: show the user each `shots/dk-desktop-fold.png` and `shots/dk-mobile-fold.png`, ask which
  direction to take, then `L pick dk`.
- Headless: `L judge` (pairwise knockout by the critic).

Both print the best id and its critique path.

## 3. Define (loop)

Repeat:
1. Read `.design-loop/variants/<best>.html` and `.design-loop/critiques/<best>.md`.
2. Write a candidate to `.design-loop/candidate.html`. You may redesign the whole page: layout, structure,
   components, style. Close the critique's biggest gaps, structure first, then details. Keep the
   direction's aesthetic unless the critique says it fails. Keep must-keep numbers/dates verbatim and at
   least 90% of the reference text.
3. `L round .design-loop/candidate.html` (integrity check, screenshots, score call, pairwise call). It
   prints the verdict and the current best; always build the next round from the best, never from a
   rejected candidate.
4. `L decide`. `CONTINUE` → repeat. `PASS`, `STOP_MAX`, or `STOP_PLATEAU` → go to 4.

**Enrich** (optional, part of step 2, when brief `enrich` isn't `off`): if the critique calls the visuals
plain or flat, or gradients/shapes stand in for imagery, read `references/media.md` and add generated images,
a 3D/shader scene, or video (video only with `images+video`). Assets go in `.design-loop/variants/assets/`
and are referenced as `assets/<file>`. The candidate still goes through `L round` like any other. Real photos:
relight them with `M image --ref`, grade them with `M grade`, and fix any `resolution` warning `L round` prints
before touching CSS (see `references/media.md`). Tell the user which assets are AI-edited.

## 4. Deliver (only if brief `polish` is true)

Dispatch the `polish` agent (plugin agent `wildling-design:polish`; if it isn't loaded, follow
`agents/polish.md` yourself in a fresh subagent). Pass it the best page path, `.design-loop/brief.json`, and
this skill dir. It cuts what doesn't earn its place, sweeps `references/ai-tells.md`, rewrites copy through
the `L copy` detector gate, and writes `.design-loop/candidate.html`.
- Interactive: show the user its COPY before → after lines and apply any edits they want. A human reads
  every line.
- If it returns `ESCALATE`, show the reason and ask (interactive) or skip Deliver (headless).
Then `L round .design-loop/candidate.html --phase deliver`. It goes through the same acceptance rule.

## 5. Finish

`L finish` writes the best to `output_file` (or over the target; the original stays in `variants/v0.html`)
and finalizes `manifest.json`. Report: winner id, score, stop reason, output path, and critic cost from
`.design-loop/log.jsonl`.

If any `L` command exits non-zero, show its error. An `AUTH ERROR` means `claude -p` is not logged in:
stop and tell the user.
