# Media: images, 3D scenes, video (Define → Enrich)

Coding agents love to write code and rarely use images. They fall back on gradients, shapes, and basic
patterns, which are all strong signs of an AI-made page. Real imagery and motion show more than surface-level
effort. `M` below means `python3 <this skill dir>/scripts/media.py`.

## When
- The brief's `enrich` isn't `off`, **and**
- the critique calls the visuals plain or flat, or gradients/shapes/patterns are standing in for imagery.
- Motion only when `enrich` is `images+video`.

Start with `M doctor --live`. It lists the usable providers per capability. If `openai` shows a `run:` note, stop
and give the user that login command (it's a browser sign-in they must do themselves).

## Images
Working prompt (from the source article): "The design is pretty plain. Add more personality using image
generation. Consider shaders or 3D effects in combination with images to create more interesting visuals."
1. Write a specific image prompt that fits the page's aesthetic: subject, composition, lighting, material,
   palette. If the image will be cut out, ask for a solid or plain background.
2. `M image --prompt "…" --out .design-loop/variants/assets/<name>.png --n 2`
3. `M compare <a> <b> --kind image`. Opus picks one. Use the winner and keep the loser for the log.
4. Reference assets relatively (`assets/<name>.png`) so screenshots load them and `finish` ships them.

## Real photos: relight, grade, and size
Real photos of the real place usually beat generated ones. Three tools make them hold up:

**Relight** (same place, different time of day or season): `M image --ref photo.jpg --prompt "…" --out
assets/x.png` (`--ref` repeats). Prompt template:
> photoreal edit of the attached photo; keep the exact composition, camera position, and every object; change
> only the time of day/season to <X>; <lighting specifics>; no people, no text; natural grain; <aspect>

Field results came back at 1024×1536, 1536×1024, and 1122×1402 and read as the real place. A relit image of a
real place is illustrative, not documentary: tell the user which assets are AI-edited.

**Grade** (one reproducible look across every still and clip): `M grade in.jpg --out assets/x.jpg --look
dusk|warm-evening|cool-day|none [--vignette 0..1] [--bloom 0..1]`. Video keeps no audio and ships as H.264
with `+faststart`. `warm-evening` with `--bloom 0.5 --vignette 0.5` is the lit-interior look. Keep grades
subtle: heavy teal/orange, strong vignettes, and crushed highlights got called "sludge" by the critic.

**Size**: `L round` prints a `resolution` warning when an asset in `variants/assets/` is drawn more than 1.3×
its own width. The critic will call it blurry every round and no CSS fixes it. Swap in a larger source, or a
landscape crop for a landscape box (a 720 px portrait clip stretched across a 1440 px hero was the single
biggest score drag in the field run).

## Motion ladder
**1. 3D/shader scene (default, always available).** You write it: Three.js from jsDelivr (pin the version)
or a raw WebGL/GLSL shader. Use this for geometric, glassy, particle, product-like, or abstract motion. The
article's crystal that "splinter[s] apart and slowly spin[s] around" with refraction and caustics belongs here.
- Live in the page: embed the scene. Drive time from `window.__t ?? performance.now()/1000` so it can be
  recorded too. Respect `prefers-reduced-motion` with a still frame.
- As a clip: `M record scene.html --out assets/x.mp4 --seconds 6 --fps 30 --bg 00ff00` →
  `M key assets/x.mp4` → a transparent webm you can layer anywhere without it looking like a video.
- For glass or refraction, render the scene **over the page's own background colors** first so the
  refraction is baked in, then key or matte it.

**2. Generative video (photoreal or organic footage code can't fake).**
`M video --prompt "…" --out assets/x.mp4 [--first frame.png] [--last frame.png]`. Exit 3 means no provider
is filled in `.env.agents`. Fall back to rung 1 or skip motion.
- Looping graphic: generate on a solid background, then `M key` (the chroma-key trick).
- Transitions between states (the article's suitcase: floating high up → lands and pops open → contents
  drop in from the top): generate the first frame with `M image`, put one prompt per transition in a JSON
  list, and run `M chain --prompts t.json --first f.png --outdir assets/suitcase`. Each clip's last frame
  seeds the next, so the sequence continues without a cut. `M scrub` each clip, then map scroll position to
  `video.currentTime`.
- Pick a model known for physics and consistency. For motion that tracks scroll, prefer steady camera moves.

**3. Compare.** `M compare a.mp4 b.mp4 --kind video` (Opus reads 8-frame contact sheets). `M frames` alone
gives you a sheet to check your own work frame by frame.

## Keys (from the source article)
- Keys live in the gitignored `.env.agents` (template: `.env.agents.example`). Never paste them into chat,
  code, or the page. `loop.py round` rejects any candidate that contains one.
- Use a separate key per agent with a tight spend limit, so a leak or misuse is cheap and the key can be
  revoked without disrupting other work.
- Subscriptions beat API keys where they exist: Codex bills the ChatGPT plan. `media.py` strips
  `OPENAI_API_KEY` from Codex's environment for exactly this reason.
