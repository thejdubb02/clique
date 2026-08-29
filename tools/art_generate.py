#!/usr/bin/env python3
"""Draw the character art for the themes, through OpenRouter.

A maintainer tool, not part of the panel: nothing shipped imports it, and it is
here so the prompts survive. They are the part worth keeping. Regenerating a
character from scratch without them produces something that does not match the
other six, or worse, produces the copyrighted original.

    OPENROUTER_API_KEY=... python3 tools/art_generate.py [name ...]
    python3 tools/art_prep.py raw/plumber.png clique/web/art/plumber.png

The key is a capped OpenRouter child key (`clique-art`, $5 hard cap, in
Vaultwarden and in agent-infra/llm-registry.yaml). Roughly seven cents an
image, so a full set of seven is about fifty cents.

Two things are load-bearing and neither is obvious:

**The originality instruction.** Ask for "a moustachioed plumber in a red cap
and blue dungarees" and the model returns Nintendo's Mario, line for line: same
face, same moustache, same proportions. That is where the phrase points. This
package is published publicly, so every character prompt carries ORIGINAL,
which tells it to take the archetype and invent the face, costume and
proportions fresh. Do not remove it, and do not add it to `tetris` - it says
"character", and asking for four tetrominoes with it attached returns a
teenager in a blue jacket.

**The magenta field.** The image models do not reliably return real
transparency, so each is drawn on a flat key colour that `art_prep.py` removes.
A white or transparent-looking background that turns out to be white is the one
mistake that cannot be undone later: it hangs a white card in the corner of
somebody's dark terminal.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

OUT = Path(os.environ.get("ART_OUT", Path(__file__).resolve().parents[1] / "raw"))
MODEL = os.environ.get("ART_MODEL", "google/gemini-3.1-flash-image")
KEY_COLOUR = "pure vivid magenta (#FF00FF)"

STYLE = (
    "Clean hand-drawn anime illustration, modern digital anime style. "
    "Crisp confident line art, flat cel shading with one clear shadow tone, "
    "vivid saturated colour. Full body, single character, standing, facing the "
    "viewer, centred, head to toe fully inside the frame with a small margin. "
    f"The background must be a completely flat solid field of {KEY_COLOUR} "
    "with absolutely nothing else in it: no scenery, no ground, no shadow on "
    "the ground, no gradient, no border, no text, no watermark, no signature. "
    "Do not use magenta anywhere on the character itself. "
    "Bold readable silhouette, strong shapes, no tiny fussy detail."
)

# The guardrail, and it is not decoration. Asked plainly for "a moustachioed
# plumber in a red cap and blue dungarees", the model returned Nintendo's
# Mario, line for line: same face, same moustache, same proportions. That is
# not inspiration, it is the character, and this package is published under
# Justin's name. The archetype is ours to use; the likeness is not.
ORIGINAL = (
    " This must be an ORIGINAL character design. Do NOT draw, copy, trace or "
    "closely resemble any existing video game, anime, film or book character, "
    "and do not reproduce anyone's mascot. Take only the broad archetype and "
    "invent the face, the costume details, the hair and the proportions fresh. "
    "It should read as a new character a fan might have designed, clearly not "
    "the famous one."
)

SUBJECTS = {
    "plumber": (
        "An original character: a young, lean plumber adventurer in a red "
        "peaked cap, a red shirt with rolled sleeves, blue denim dungarees "
        "with two round gold buttons, thick work gloves and heavy brown "
        "boots. Tousled dark hair under the cap, a neat short moustache, "
        "welding goggles pushed up on the cap brim, a leather tool belt with "
        "a big pipe wrench hanging from it. Cheerful and scrappy."
    ),
    "triforce": (
        "An original character: a young blond elf swordsman in a soft green "
        "hood with a long trailing point, a layered green tunic over cream "
        "leggings, a wide brown leather belt and tall brown boots. Long "
        "pointed elf ears, bright green eyes, a leaf-shaped clasp at the "
        "throat. Holding a slim silver longsword upright in one hand and a "
        "round blue shield with a gold sunburst boss in the other. Brave."
    ),
    "fellowship": (
        "An original character: an old wizard dressed entirely in STONE GREY "
        "and dusty cream, with no green anywhere on him. A long white beard, "
        "a wide floppy pointed grey felt hat and a long grey wool travelling "
        "robe belted with pale rope. Kind weathered face, bright blue eyes, "
        "heavy white eyebrows, a small tan leather satchel at his hip. "
        "Leaning on a tall gnarled pale wooden staff with a warm amber stone "
        "bound into the top. The only warm colour on him is that amber stone "
        "and the tan leather. Wise and warm."
    ),
    "drizzt": (
        "An original character: a dark elf ranger with deep charcoal-violet "
        "skin and long flowing white hair, glowing lavender eyes, long "
        "pointed ears. Dark purple studded leather armour under a deep indigo "
        "hooded cloak with a silver clasp. Wielding two curved silver "
        "scimitars, one in each hand, held out to the sides. Agile, watchful."
    ),
    "pacman": (
        "An original design: a plump round bright yellow creature, a fat "
        "yellow ball with a wide open wedge mouth, two small round eyes and "
        "tiny stubby feet, floating beside a plump round pink ghost with a "
        "wavy scalloped bottom edge, two little nub arms and big expressive "
        "blue eyes. Two cute arcade-style creatures side by side, cheerful."
    ),
    "tetris": (
        "Four glossy falling tetromino blocks arranged in a loose diagonal "
        "cascade: a cyan straight four-block bar, a purple T-shaped piece, a "
        "yellow two-by-two square and a green S-shaped piece. Each block "
        "bevelled with a bright highlight edge and a darker shaded edge, with "
        "a soft glow. No characters, just the four pieces. Geometric shapes "
        "only, so the originality note does not apply here."
    ),
    "aincrad": (
        "An original character: a lean teenage swordsman with messy black "
        "hair swept over one eye and sharp bright cyan eyes, in a long black "
        "high-collared coat with glowing cyan piping along the seams, a short "
        "cyan scarf, black trousers and buckled boots. Dual wielding, a matte "
        "black longsword in one hand and a translucent blue-green crystal "
        "longsword in the other, both held ready. Cool and composed."
    ),
}


def generate(name: str, prompt: str) -> tuple[Path, dict]:
    body = json.dumps({
        "model": MODEL,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": f"{prompt}{'' if name == 'tetris' else ORIGINAL}\n\n{STYLE}"}],
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=body,
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://useclique.dev",
            "X-Title": "CLIque theme art",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        payload = json.load(r)
    msg = payload["choices"][0]["message"]
    for image in msg.get("images") or []:
        url = (image.get("image_url") or {}).get("url", "")
        if url.startswith("data:"):
            OUT.mkdir(parents=True, exist_ok=True)
            path = OUT / f"{name}.png"
            path.write_bytes(base64.b64decode(url.split(",", 1)[1]))
            return path, payload.get("usage", {})
    raise SystemExit(f"{name}: no image came back. {json.dumps(msg)[:300]}")


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SUBJECTS)
    for name in wanted:
        path, usage = generate(name, SUBJECTS[name])
        print(f"  {name:<11} {path.stat().st_size // 1024:>5} KB   {usage}")
