# wildling-design

A Claude Code plugin (`/design-loop`) that takes an HTML page, existing or new, to studio-level design quality:

0. **Ambition gate** (plugin hook). When a prompt gives broad design direction ("build me a landing page for
   my productivity app"), Claude first asks "Want me to make this request more ambitious?" On yes, it
   rewrites the request around one vivid outside inspiration. Specific edits ("remove the footer", "make the
   logo bigger", "change the font to Inter") never trigger it. The classifier is regex, in
   `hooks/ambition_gate.py`.
1. **Onboard.** Asks for the page's purpose, audience, mood, must-keep content and moodboard, plus the
   **pass grade** (default 9/10), the round cap, the number of directions, and `enrich` (images, video, off).
2. **Discover.** Each direction starts from a random seed string (String Seed of Thought, Sakana AI) so the
   directions come out genuinely different. You pick one.
3. **Define.** Full-page redesign rounds. Each round a critic judges the page in a fresh `claude -p` process,
   seeing screenshots only: no code, no brief, no earlier critiques, and never the pass grade. Each fold is
   shot twice, on load and again after timed motion settles (`settle_ms`, default 8 s), so autoplay heroes and
   crossfades are judged by what a visitor actually sees. A version only
   becomes the new best if it
   - wins a side-by-side comparison against the current best,
   - scores at least as high, and
   - passes a check that none of the original text, numbers or dates were lost, and that no API key leaked
     into the page.

   When the critique calls the page plain, rounds can **enrich** it (`scripts/media.py`): images through the
   Codex CLI on your ChatGPT plan; 3D/shader scenes Claude writes, live or recorded to video on a
   deterministic clock and chroma-keyed; generative video (Veo) if you add a key. Opus picks between
   candidates, comparing video by frame contact sheets. Real photos can be relit (`image --ref`, disclosed as
   AI-edited) and given one consistent ffmpeg look (`grade`), and each round warns when an asset is drawn
   more than 1.3× its own width, the blur no CSS can fix.
4. **Deliver.** The `polish` agent (fresh context) writes one subtraction candidate. It cuts elements that
   don't earn their place, sweeps the AI design tells (eyebrow labels, generic gradients, excessive
   containers, too many type styles…), and rewrites copy until the avoid-ai-writing detector finds no more
   issues than before.

The run stops when the score reaches the pass grade (`PASS`), hits the round cap (`STOP_MAX`), or goes 3 rounds
without a new best (`STOP_PLATEAU`).

Method from Anshu Chimala's "How to turn your AI into a world-class designer" (Lenny's Newsletter,
2026-09-01). In a blind bake-off against a theme-only, lint-gated variant, this version scored 8.0 vs 5.0 with
an independent judge.

## Requirements
- Claude Code CLI, logged in (`claude auth login`). The critic runs headless.
- Node + Google Chrome. Screenshots use `npx playwright@1.63.0` with `--channel chrome`, so no browser
  download is needed.
- Python 3 (stdlib only).
- Media (optional): ffmpeg; Codex CLI signed in with ChatGPT (`npx -y @openai/codex@0.157.1 login`, run
  through npx, no install needed). Other providers go in a gitignored `.env.agents` in your project. Leave a
  provider blank if you don't have it. `python3 skills/design-loop/scripts/media.py doctor` shows what's usable.
- Copy gate (optional): the [avoid-ai-writing](https://github.com/conorbronsdon/avoid-ai-writing) skill at
  `~/.claude/skills/avoid-ai-writing`. Without it, the copy pass runs on its written rules only.

## Install
Clone, then load the repo as a plugin (needed for the ambition hook and the polish agent):
```bash
git clone https://github.com/zog-r/wildling-design && cd wildling-design
claude --plugin-dir "$PWD"
```
Skill only (no hook, no agent):
```bash
ln -s "$PWD/skills/design-loop" ~/.claude/skills/design-loop
```

## Usage
Interactive, in any project:
```
/design-loop
```
Headless, with a brief (fields in `skills/design-loop/SKILL.md`):
```bash
claude -p "Run the design-loop skill with --brief ./brief.json"
```

Run artifacts (seeds, variants, screenshots, critiques, `manifest.json`, `log.jsonl` with per-call cost) land
in `.design-loop/` inside the project you run it in.

## Cost
Critic calls run with lean flags, so each call pays about $0.02 of fixed overhead instead of loading your whole
Claude Code config. A full run (3 directions, 6 rounds, polish) cost about $16 in the bake-off, roughly half of
it critic calls.

## Test
```bash
python3 skills/design-loop/scripts/loop.py selftest
python3 hooks/ambition_gate.py selftest
python3 skills/design-loop/scripts/media.py selftest
```

## License
MIT. See [LICENSE](LICENSE).
