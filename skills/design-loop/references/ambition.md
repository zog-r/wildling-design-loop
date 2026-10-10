# Make the request more ambitious

The user said yes. Turn their broad design request into an ambitious one. Asking a model for "something
unique" gets the same answer everyone gets. What works is a specific, vivid vision to build from, so the model
doesn't make its choices on the fly.

## Rules
- Keep every concrete fact from the original verbatim: product, audience, content, numbers, constraints.
- Anchor each rewrite in **one** outside inspiration: a video game, an interior design trend, an art
  installation, an industrial object, a film's production design, a print tradition. Name it and say **how**
  it shapes layout, type, color, and interaction.
- The page still has to do its job. Borrow the email's phrasing: "…yet somehow it should all function as a
  landing page."
- Go bolder than feels safe. If you think "there's no way this will work," you're probably on the right track.
- No generic adjectives ("modern", "clean", "sleek", "stunning") without a concrete image behind them.
- One to three sentences. It's a direction, not a spec.

## Reference prompts (tone and ambition level, from the source article)
- "…with a bold pixel art theme and stunning graphics. Each section should feel like a still from a video
  game, yet somehow it should all function as a landing page."
- "…set in an isometric living 3D city, where different features are somehow represented by neighborhoods
  or buildings."
- "…with a radically asymmetric layout, dissonant colors and typography, and uncomfortable negative space.
  Break all the rules but still make it look good."

## Flow
1. **Go broad.** Draft about 8 one-line directions, each from a different inspiration family. Keep them
   short, since the point is to spark the user's imagination. Pick the 3 that differ most.
2. **Let the user react.** AskUserQuestion: "Which direction?" with the 3 as options (label = the
   inspiration, description = the one-line rewrite). The user can pick "Other" to steer.
   - If they add reactions, sharpen the pick using them. The article's worked example, an Industrial Control
     Panel direction: the user wants it tactile (clicky, satisfying buttons, nice sounds); cartoony or
     skeuomorphic reads as tacky, so avoid it; they want consistent components and small touches that land
     the look without going overboard; gray gradients are boring, so they want more texture and some color.
     Their taste goes into the prompt as concrete dos and don'ts.
3. **Write the build prompt.** A concise prompt an agent could build a first version from. Prefix it with
   `[ambitious]` so the gate doesn't fire again, and work from it. In a design-loop run, it goes into the
   brief's `inspiration` field.
4. Append the directions that weren't picked to `.design-loop/prompt-ideas.md` (one per line, dated).
   Directions that fail today may work on the next model, so retry them when a new one ships.
