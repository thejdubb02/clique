# CLI icons

One flat shape per CLI, rendered as a **CSS mask**: only the silhouette is
used and the colour comes from the panel. That is what lets the same file
appear tinted in a CLI's colour or in neutral grey depending on the setting —
and it means gradients or multiple colours here would be flattened away.

## Where these came from

The vendor marks are from [simple-icons](https://simpleicons.org) (CC0-1.0),
copied in at build time rather than fetched at runtime: no CDN dependency, and
upgrading is a deliberate act with a diff.

| File | simple-icons slug | Used by |
|---|---|---|
| `claude.svg` | `claude` | Claude Code |
| `gemini.svg` | `googlegemini` | Gemini CLI |
| `grok.svg` | `x` | Grok CLI (xAI) |
| `codex.svg` | `openai` | Codex CLI |
| `copilot.svg` | `githubcopilot` | GitHub Copilot CLI |
| `cursor.svg` | `cursor` | Cursor CLI |
| `qwen.svg` | `alibabacloud` | Qwen Code |
| `shell.svg` | `gnubash` | Shell |
| `ollama.svg` | `ollama` | (spare) |
| `default.svg` | — | drawn here |

Trademarks belong to their owners; these identify the tool being run, which is
nominative use, and no endorsement is implied.

## CLIs with no mark

Aider, OpenCode, Goose, Crush, Amazon Q, Plandex, Droid and Cline have no
simple-icons entry. They deliberately get **no** `icon` key, and the panel
draws a letter badge in the CLI's colour instead — which looks intentional and
means adding a CLI never waits on artwork.

Adding one: drop `<name>.svg` here and set `icon = "<name>.svg"` in
`config/clis.toml`.
