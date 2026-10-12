# AI tells

Every model has patterns it overuses, and users now read them as the marks of AI slop.

## Banned: enforced in code
`scripts/tells.mjs` measures these from computed styles on every screenshot. `L round` rejects any candidate
that has one, without calling the critic, and `L finish` won't ship a page that has one. Check a page
yourself with `L tells <page.html>`, or `L tells <url>` for pages that only render with live data
(check each data state: logged in, empty, full). Don't design these in and then hope to fix them later.

| Banned | Do instead |
|---|---|
| **Accent emphasis**: a word or phrase in a heading set in italics, another color, or another font ("Make space for *better ideas*" with the last words in orange italic), or colored/other-font words in running text | One style per heading. Get emphasis from size, weight, line breaks, or placement. A plain same-color `<em>` in body copy is fine. |
| **Eyebrow text**: a small uppercase or letter-spaced label right above a heading ("EXPENSE MANAGEMENT" over a hero about expense management) | Delete it. Nine times out of ten nothing of value is lost. If the fact matters, put it in the heading or the body. |
| **Generic background gradients**: an opaque gradient as a large surface (the blue-to-cyan hero) | Background images, patterns, or a solid color. A translucent scrim over a photo is fine. |
| **Nested cards**: a bordered, shadowed, or filled box inside another one | Flatter layouts: grids or tiles. Let whitespace do the grouping. |
| **Too many fonts**: more than 2 families on the page | 1–2 fonts and 2–4 text styles until you truly need more. |

## Also sweep for (judgment, not gated: try the alternative, keep what looks better)
- Glows and glowy effects: behind the page, on progress bars, around buttons
- Random colors and highlights on words in running text
- Labels that repeat what an image already shows
- Custom buttons, inputs and controls that look worse than native platform ones
- Gradients, shapes and basic patterns used *in place of* real imagery (see `media.md`)
- Purple-blue gradients; emoji used as bullets or icons
- Three identical cards (icon + title + blurb); pill badges everywhere
- Everything centered; the generic hero-left / graphic-right split
- Decoration that doesn't earn its place

## Subtract
AI adds and rarely removes. Restraint is what reads as premium. For every element, ask what really needs to be
there. Putting less on screen often communicates more. Keep every must-keep fact verbatim.
