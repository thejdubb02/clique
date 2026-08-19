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
 */
window.CLIQUE_THEMES = {
  "": {
    label: "clique dark", base: "dark",
    panel: { bg: "#1e1e1e", panel: "#252526", row: "#2a2d2e", sel: "#04395e",
             field: "#3c3c3c", fg: "#cccccc", dim: "#8b8b8b", line: "#333",
             accent: "#0078d4" },
    term: { background: "#1e1e1e", foreground: "#cccccc", cursor: "#cccccc",
            selectionBackground: "#264f78" },
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
            blue: "#bd93f9", magenta: "#ff79c6", cyan: "#8be9fd", white: "#f8f8f2" },
  },

  nord: {
    label: "Nord", base: "dark",
    panel: { bg: "#2e3440", panel: "#3b4252", row: "#434c5e", sel: "#4c566a",
             field: "#434c5e", fg: "#eceff4", dim: "#8fa1b3", line: "#3b4252",
             accent: "#88c0d0" },
    term: { background: "#2e3440", foreground: "#d8dee9", cursor: "#d8dee9",
            selectionBackground: "#434c5e",
            black: "#3b4252", red: "#bf616a", green: "#a3be8c", yellow: "#ebcb8b",
            blue: "#81a1c1", magenta: "#b48ead", cyan: "#88c0d0", white: "#e5e9f0" },
  },

  gruvbox: {
    label: "Gruvbox", base: "dark",
    panel: { bg: "#282828", panel: "#32302f", row: "#3c3836", sel: "#504945",
             field: "#3c3836", fg: "#ebdbb2", dim: "#a89984", line: "#3c3836",
             accent: "#fabd2f" },
    term: { background: "#282828", foreground: "#ebdbb2", cursor: "#ebdbb2",
            selectionBackground: "#504945",
            black: "#282828", red: "#cc241d", green: "#98971a", yellow: "#d79921",
            blue: "#458588", magenta: "#b16286", cyan: "#689d6a", white: "#a89984" },
  },

  tokyonight: {
    label: "Tokyo Night", base: "dark",
    panel: { bg: "#1a1b26", panel: "#16161e", row: "#232433", sel: "#283457",
             field: "#232433", fg: "#c0caf5", dim: "#787c99", line: "#232433",
             accent: "#7aa2f7" },
    term: { background: "#1a1b26", foreground: "#c0caf5", cursor: "#c0caf5",
            selectionBackground: "#283457",
            black: "#15161e", red: "#f7768e", green: "#9ece6a", yellow: "#e0af68",
            blue: "#7aa2f7", magenta: "#bb9af7", cyan: "#7dcfff", white: "#a9b1d6" },
  },

  solarized: {
    label: "Solarized Dark", base: "dark",
    panel: { bg: "#002b36", panel: "#073642", row: "#0a4553", sel: "#0f5666",
             field: "#073642", fg: "#93a1a1", dim: "#657b83", line: "#073642",
             accent: "#268bd2" },
    term: { background: "#002b36", foreground: "#93a1a1", cursor: "#93a1a1",
            selectionBackground: "#0f5666",
            black: "#073642", red: "#dc322f", green: "#859900", yellow: "#b58900",
            blue: "#268bd2", magenta: "#d33682", cyan: "#2aa198", white: "#eee8d5" },
  },

  paper: {
    label: "Paper (light)", base: "light",
    panel: { bg: "#fdf6e3", panel: "#f4ecd8", row: "#eee5cd", sel: "#dcd3bb",
             field: "#fffbf0", fg: "#3b3a36", dim: "#7d7a70", line: "#e0d7c0",
             accent: "#b58900" },
    term: { background: "#fdf6e3", foreground: "#3b3a36", cursor: "#3b3a36",
            selectionBackground: "#dcd3bb",
            black: "#3b3a36", red: "#dc322f", green: "#657b0d", yellow: "#b58900",
            blue: "#268bd2", magenta: "#d33682", cyan: "#2aa198", white: "#7d7a70" },
  },
};
