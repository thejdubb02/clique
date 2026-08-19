# CLI icons

One flat shape per CLI, rendered as a **CSS mask**: only the silhouette is
used and the colour comes from the panel. That is what lets the same file
appear tinted in a CLI's colour or in neutral grey depending on the setting —
and it means gradients or multiple colours here would be flattened away.

A PNG works as well as an SVG, provided it carries an alpha channel: a mask
reads alpha, not pixels.

## Where these came from

Copied in at build time rather than fetched at runtime: no CDN dependency, and
upgrading is a deliberate act with a diff.

| File | Source | Used by |
|---|---|---|
| `claude.svg` | simple-icons `claude` | Claude Code |
| `gemini.svg` | simple-icons `googlegemini` | Gemini CLI |
| `grok.svg` | simple-icons `x` | Grok CLI (xAI) |
| `codex.svg` | simple-icons `openai` | Codex CLI |
| `copilot.svg` | simple-icons `githubcopilot` | GitHub Copilot CLI |
| `cursor.svg` | simple-icons `cursor` | Cursor CLI |
| `qwen.svg` | simple-icons `alibabacloud` | Qwen Code |
| `shell.svg` | simple-icons `gnubash` | Shell |
| `opencode.svg` | opencode.ai favicon | OpenCode |
| `goose.svg` | block/goose desktop icon | Goose |
| `droid.svg` | factory.ai favicon | Factory Droid |
| `cline.png` | cline/cline repo icon (RGBA) | Cline |
| `ollama.svg` | simple-icons `ollama` | (spare) |
| `default.svg` | drawn here | fallback |

simple-icons is CC0-1.0. The rest are the projects\' own marks, used to
identify the tool being run — nominative use, no endorsement implied.

## Two that were deliberately not taken

- **Aider** publishes a wordmark (200x60), not a mark. Squashed into a 13px
  square it is illegible, and cropping someone\'s logo to invent a glyph they
  did not draw is worse than not having one.
- **Crush, Plandex and Amazon Q** have no icon at a stable URL that reduces to
  a clean silhouette.

All four draw a **letter badge** in the CLI\'s colour instead. That is a real
design choice, not a gap: it is uniform, it is legible at 13px, and it means
adding a CLI never waits on artwork.

## Adding one

Drop `<name>.svg` (or an RGBA `.png`) here and set `icon = "<name>.svg"` in
`config/clis.toml`. Leave `icon` out to get the letter badge.

**Check it as a silhouette before committing it.** Logos with a full-canvas
background rectangle flatten to a solid square — `droid.svg` needed its
background stripped for exactly this reason.
