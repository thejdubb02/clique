# CLI icons

One flat shape per CLI, 24×24, drawn in black on transparency.

These are rendered as **CSS masks**, not images: only the silhouette is used
and the colour comes from the panel, which is what lets the same icon appear
tinted in a CLI's colour or in neutral grey depending on the user's setting.
So there is no point adding gradients or multiple colours here — they will be
flattened to alpha.

They are deliberately simplified marks, not the vendors' logo files: enough to
tell four tabs apart at 14 pixels, which is the whole job.

Adding one: drop `<name>.svg` here and reference it as `icon = "<name>.svg"`
in `config/clis.toml`. Anything unset falls back to `default.svg`.
