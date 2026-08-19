/* Preset palettes.
 *
 * Each theme supplies the panel's CSS variables and the terminal's ANSI
 * palette together, because a panel and a terminal in different colour worlds
 * looks like a bug rather than a choice.
 *
 * `base` says whether the theme is light or dark, so the panel can set
 * color-scheme correctly — that is what makes native scrollbars, form controls
 * and the terminal's own selection colour agree with the rest.
 *
 * Adding one is a block here. Nothing else needs to change.
 *
 * A `term` block must carry **all sixteen** ANSI colours, brights included.
 * Setting only the eight base ones leaves xterm using its own defaults for the
 * brights — and CLIs lean on brights heavily, so the result is a themed panel
 * wrapped around output in somebody else's colours, which is exactly the
 * complaint that got these filled in.
 *
 * `on-accent`, `scrim` and `shadow` are NOT set here. They follow mechanically
 * from the theme and are derived in applySettings(), so a theme stays one
 * block and cannot forget them.
 */
window.CLIQUE_THEMES = {
  "": {
    label: "clique dark", base: "dark",
    panel: { bg: "#1e1e1e", panel: "#252526", row: "#2a2d2e", sel: "#04395e",
             field: "#3c3c3c", fg: "#cccccc", dim: "#8b8b8b", line: "#333",
             accent: "#0078d4" },
    term: { background: "#1e1e1e", foreground: "#cccccc", cursor: "#cccccc",
            selectionBackground: "#264f78",
            black: "#000000", red: "#cd3131", green: "#0dbc79", yellow: "#e5e510",
            blue: "#2472c8", magenta: "#bc3fbc", cyan: "#11a8cd", white: "#e5e5e5",
            brightBlack: "#666666", brightRed: "#f14c4c", brightGreen: "#23d18b",
            brightYellow: "#f5f543", brightBlue: "#3b8eea", brightMagenta: "#d670d6",
            brightCyan: "#29b8db", brightWhite: "#e5e5e5" },
  },

  light: {
    label: "clique light", base: "light",
    panel: { bg: "#ffffff", panel: "#f3f3f3", row: "#e8e8e8", sel: "#cfe6ff",
             field: "#ffffff", fg: "#24292f", dim: "#6e7781", line: "#d8dee4",
             accent: "#0969da" },
    term: { background: "#ffffff", foreground: "#24292f", cursor: "#24292f",
            selectionBackground: "#b6d7ff",
            black: "#24292f", red: "#cf222e", green: "#116329", yellow: "#4d2d00",
            blue: "#0969da", magenta: "#8250df", cyan: "#1b7c83", white: "#6e7781",
            brightBlack: "#57606a", brightRed: "#a40e26", brightGreen: "#1a7f37",
            brightYellow: "#633c01", brightBlue: "#218bff", brightMagenta: "#a475f9",
            brightCyan: "#3192aa", brightWhite: "#8c959f" },
  },

  dracula: {
    label: "Dracula", base: "dark",
    panel: { bg: "#282a36", panel: "#21222c", row: "#343746", sel: "#44475a",
             field: "#343746", fg: "#f8f8f2", dim: "#6272a4", line: "#191a21",
             accent: "#bd93f9" },
    term: { background: "#282a36", foreground: "#f8f8f2", cursor: "#f8f8f2",
            selectionBackground: "#44475a",
            black: "#21222c", red: "#ff5555", green: "#50fa7b", yellow: "#f1fa8c",
            blue: "#bd93f9", magenta: "#ff79c6", cyan: "#8be9fd", white: "#f8f8f2",
            brightBlack: "#6272a4", brightRed: "#ff6e6e", brightGreen: "#69ff94", brightYellow: "#ffffa5",
            brightBlue: "#d6acff", brightMagenta: "#ff92df", brightCyan: "#a4ffff", brightWhite: "#ffffff" },
  },

  nord: {
    label: "Nord", base: "dark",
    panel: { bg: "#2e3440", panel: "#3b4252", row: "#434c5e", sel: "#4c566a",
             field: "#434c5e", fg: "#eceff4", dim: "#8fa1b3", line: "#3b4252",
             accent: "#88c0d0" },
    term: { background: "#2e3440", foreground: "#d8dee9", cursor: "#d8dee9",
            selectionBackground: "#434c5e",
            black: "#3b4252", red: "#bf616a", green: "#a3be8c", yellow: "#ebcb8b",
            blue: "#81a1c1", magenta: "#b48ead", cyan: "#88c0d0", white: "#e5e9f0",
            brightBlack: "#4c566a", brightRed: "#bf616a", brightGreen: "#a3be8c", brightYellow: "#ebcb8b",
            brightBlue: "#81a1c1", brightMagenta: "#b48ead", brightCyan: "#8fbcbb", brightWhite: "#eceff4" },
  },

  gruvbox: {
    label: "Gruvbox", base: "dark",
    panel: { bg: "#282828", panel: "#32302f", row: "#3c3836", sel: "#504945",
             field: "#3c3836", fg: "#ebdbb2", dim: "#a89984", line: "#3c3836",
             accent: "#fabd2f" },
    term: { background: "#282828", foreground: "#ebdbb2", cursor: "#ebdbb2",
            selectionBackground: "#504945",
            black: "#282828", red: "#cc241d", green: "#98971a", yellow: "#d79921",
            blue: "#458588", magenta: "#b16286", cyan: "#689d6a", white: "#a89984",
            brightBlack: "#928374", brightRed: "#fb4934", brightGreen: "#b8bb26", brightYellow: "#fabd2f",
            brightBlue: "#83a598", brightMagenta: "#d3869b", brightCyan: "#8ec07c", brightWhite: "#ebdbb2" },
  },

  tokyonight: {
    label: "Tokyo Night", base: "dark",
    panel: { bg: "#1a1b26", panel: "#16161e", row: "#232433", sel: "#283457",
             field: "#232433", fg: "#c0caf5", dim: "#787c99", line: "#232433",
             accent: "#7aa2f7" },
    term: { background: "#1a1b26", foreground: "#c0caf5", cursor: "#c0caf5",
            selectionBackground: "#283457",
            black: "#15161e", red: "#f7768e", green: "#9ece6a", yellow: "#e0af68",
            blue: "#7aa2f7", magenta: "#bb9af7", cyan: "#7dcfff", white: "#a9b1d6",
            brightBlack: "#414868", brightRed: "#ff7a93", brightGreen: "#b9f27c", brightYellow: "#ff9e64",
            brightBlue: "#7da6ff", brightMagenta: "#bb9af7", brightCyan: "#0db9d7", brightWhite: "#c0caf5" },
  },

  solarized: {
    label: "Solarized Dark", base: "dark",
    panel: { bg: "#002b36", panel: "#073642", row: "#0a4553", sel: "#0f5666",
             field: "#073642", fg: "#93a1a1", dim: "#657b83", line: "#073642",
             accent: "#268bd2" },
    term: { background: "#002b36", foreground: "#93a1a1", cursor: "#93a1a1",
            selectionBackground: "#0f5666",
            black: "#073642", red: "#dc322f", green: "#859900", yellow: "#b58900",
            blue: "#268bd2", magenta: "#d33682", cyan: "#2aa198", white: "#eee8d5",
            brightBlack: "#002b36", brightRed: "#cb4b16", brightGreen: "#586e75", brightYellow: "#657b83",
            brightBlue: "#839496", brightMagenta: "#6c71c4", brightCyan: "#93a1a1", brightWhite: "#fdf6e3" },
  },

  /* Green phosphor on black. Red stays red and yellow stays yellow, because a
   * terminal where an error cannot be told from a diff is a costume, not a
   * theme — everything that is not carrying meaning leans green. */
  trinity: {
    label: "Trinity", base: "dark",
    /* Monochrome by design, so it owns the greyscale ramp too. Without this a
     * CLI that paints its background with colour 233 — Grok does — renders
     * neutral #121212 in the middle of a green terminal, and the theme reads
     * as not having applied. See extendedAnsi in app.js. */
    tint_greys: true,
    panel: { bg: "#050705", panel: "#0a0f0a", row: "#0f1a10", sel: "#0d3b18",
             field: "#0d1a0e", fg: "#b9ffc9", dim: "#4f8f5f", line: "#142a16",
             accent: "#00ff41" },
    term: { background: "#050705", foreground: "#00e83a", cursor: "#00ff41",
            selectionBackground: "#0d3b18",
            black: "#0a0f0a", red: "#ff4d4d", green: "#00ff41", yellow: "#9fef00",
            blue: "#00b36b", magenta: "#4fe37f", cyan: "#00e5a0", white: "#b9ffc9",
            brightBlack: "#2e5c35", brightRed: "#ff6b6b", brightGreen: "#6bff8f",
            brightYellow: "#d4ff4f", brightBlue: "#00d98a", brightMagenta: "#7dffa8",
            brightCyan: "#5cffd0", brightWhite: "#e8ffee" },
  },

  paper: {
    label: "Paper (light)", base: "light",
    panel: { bg: "#fdf6e3", panel: "#f4ecd8", row: "#eee5cd", sel: "#dcd3bb",
             field: "#fffbf0", fg: "#3b3a36", dim: "#7d7a70", line: "#e0d7c0",
             accent: "#b58900" },
    term: { background: "#fdf6e3", foreground: "#3b3a36", cursor: "#3b3a36",
            selectionBackground: "#dcd3bb",
            black: "#3b3a36", red: "#dc322f", green: "#657b0d", yellow: "#b58900",
            blue: "#268bd2", magenta: "#d33682", cyan: "#2aa198", white: "#7d7a70",
            brightBlack: "#002b36", brightRed: "#cb4b16", brightGreen: "#586e75", brightYellow: "#657b83",
            brightBlue: "#839496", brightMagenta: "#6c71c4", brightCyan: "#93a1a1", brightWhite: "#fdf6e3" },
  },
};
