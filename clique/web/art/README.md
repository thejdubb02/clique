# Theme artwork

A drawing a theme hangs in the bottom-right of the pane, behind the text.

A theme refers to one by file name and nothing else:

    art: { src: "art/plumber.png" }

Anything here is served as-is and packaged into the wheel, so treat this
directory as shipped product rather than a scratch folder.

## What works

**Transparent PNG.** No background, no card, no drop shadow baked in: the
terminal is the background, and a white rectangle behind the figure is the one
thing that cannot be recovered later.

**Bold.** It is drawn at about nine percent opacity in the corner of a pane
somebody is reading. Silhouette and large shapes survive that. Thin linework,
fine gradients and small text do not, and adding them costs file size for
something nobody will see.

**Big enough, not huge.** It displays at up to 340 by 240, so roughly 700px on
the long edge is plenty. `tools/art_prep.py` keys out the background, crops,
scales and quantises, and is worth running on anything that arrives straight
from an illustrator: a 4MB export in a package whose whole argument is that it
is small is a bad trade.

`tools/art_generate.py` is how the seven here were drawn, and holds the prompts.
Read its docstring before regenerating one: the originality instruction in it is
load-bearing, not decoration.

## What the panel does with it

Laid over the terminal at low opacity, not blended. The grid-drawn figures use
`lighten`/`darken`, which keeps text perfectly untouched but only works because
every colour in them is a mid-tone by construction. A real drawing has blacks
and whites in it and those blends eat exactly those, so artwork is composited
normally and the text loses a little contrast where the two overlap. That is
the trade for taking art as it comes.
