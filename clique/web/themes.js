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
             field: "#ffffff", fg: "#24292f", dim: "#57606a", line: "#d8dee4",
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
     * as not having applied.
     *
     * A number rather than `true`: the ramp keeps each step's lightness and
     * only takes the hue, so this is how far. Low, because an application
     * reaching for 232-255 wants a *subtle* shade, and a strong tint turns
     * that into a wash that the text then has to compete with. See
     * extendedAnsi in app.js. */
    tint_greys: 0.18,
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

  /* ------------------------------------------------------------ characters
   *
   * The seven below carry an `art` block as well as a palette: a chunky pixel
   * figure watermarked into the bottom-right of the pane, behind the text.
   *
   * The format is a palette and a grid. One character per cell, `.` for a
   * hole, and every row exactly `w` wide — tools/theme_check.py fails the
   * build otherwise, because a ragged row draws a ragged character and
   * nothing else would catch it.
   *
   * Two rules decide the colours, and both come from how it is composited.
   * The layer blends with `lighten` on a dark theme and `darken` on a light
   * one, which is what puts it *behind* the text rather than over it: text is
   * the brightest thing on a dark pane and the darkest on a light one, so it
   * wins the blend either way and stays exactly as legible as it was.
   *
   *   1. Every colour is a mid-tone. Near-black detail is invisible on a dark
   *      theme and near-white detail is invisible on a light one, so a black
   *      coat gets lifted to slate and reads as black anyway at this opacity.
   *   2. Detail below about two cells is noise. This is a watermark at
   *      thirteen percent, not a portrait: silhouette and flat blocks survive,
   *      single-pixel eyes mostly do not.
   *
   * Drawn here rather than borrowed. These are our own figures in the spirit
   * of the thing each theme is named for, which is also the only version of
   * this that is ours to ship. */

  plumber: {
    label: "Plumber", base: "dark",
    panel: { bg: "#141a2e", panel: "#1b2340", row: "#25304f", sel: "#33406b",
             field: "#1f2846", fg: "#e8ecf7", dim: "#8d99c0", line: "#2a3457",
             accent: "#e5372c" },
    term: { background: "#141a2e", foreground: "#e8ecf7", cursor: "#f2c14e",
            selectionBackground: "#33406b",
            black: "#1b2340", red: "#e5372c", green: "#4caf50", yellow: "#f2c14e",
            blue: "#3d7bd9", magenta: "#d95f9e", cyan: "#49c4d6", white: "#dbe2f2",
            brightBlack: "#5b688f", brightRed: "#ff5c4d", brightGreen: "#6ede72",
            brightYellow: "#ffd76a", brightBlue: "#5f9dff", brightMagenta: "#ff86c2",
            brightCyan: "#6fe0ef", brightWhite: "#ffffff" },
    art: {
      w: 14, h: 16,
      pal: { R: "#e5372c", M: "#7a4a22", S: "#f2b98d", E: "#6b4326",
             B: "#3d7bd9", Y: "#f2c14e", W: "#d8dfea", K: "#8a5a2b" },
      rows: [
        "....RRRRRR....",
        "...RRRRRRRR...",
        "...MMMSSSSS...",
        "..MMSSSSSSSS..",
        "..MSSESSSESS..",
        "..MSSSSSSSSS..",
        "...MMMMMMMM...",
        "....SSSSSS....",
        "..RRRBBBRRR...",
        ".WRRRBYBRRRW..",
        ".WRRBBBBBRRW..",
        ".WRRBBBBBRRW..",
        "...BBB.BBB....",
        "...BBB.BBB....",
        "..KKKK.KKKK...",
        ".KKKKK.KKKKK..",
      ],
    },
  },

  triforce: {
    label: "Triforce", base: "dark",
    panel: { bg: "#101a14", panel: "#16241b", row: "#1e3125", sel: "#2b4a33",
             field: "#1a2b20", fg: "#e4f0e2", dim: "#87a58e", line: "#22382a",
             accent: "#f2c14e" },
    term: { background: "#101a14", foreground: "#dcecd8", cursor: "#f2c14e",
            selectionBackground: "#2b4a33",
            black: "#16241b", red: "#d9534f", green: "#3fa34d", yellow: "#e0b13c",
            blue: "#3f8fd0", magenta: "#b06fc4", cyan: "#4bb8a8", white: "#cfe0cb",
            brightBlack: "#4b6b54", brightRed: "#ff7a72", brightGreen: "#63d477",
            brightYellow: "#ffd76a", brightBlue: "#68b6f5", brightMagenta: "#d59aec",
            brightCyan: "#6fe0cd", brightWhite: "#f2fbf0" },
    art: {
      w: 18, h: 16,
      pal: { G: "#3fa34d", H: "#e8c15a", S: "#f0c49b", E: "#6b5a3a",
             L: "#2f6fd0", Y: "#f2c14e", B: "#8a5a2b", D: "#dfe6ec" },
      rows: [
        ".......GGG........",
        "......GGGGG.......",
        ".....GGGGGGG..D...",
        ".....HHSSSSH..D...",
        ".....HSESESH..D...",
        ".....HSSSSSH..D...",
        "......SSSSS...D...",
        "..LLL.GGGGG...D...",
        ".LLLLLGGGGG...D...",
        ".LLYLLGGGGG..DDD..",
        ".LLLLLGGGGG...B...",
        "..LLL.GGGGG...B...",
        "......BBBBB.......",
        "......GGGGG.......",
        "......SS.SS.......",
        ".....BBB.BBB......",
      ],
    },
  },

  /* Named for the company rather than the ring, because the ring is the part
   * that is drawn and a theme called after it would be the one thing on this
   * list claiming to be the artefact itself. */
  fellowship: {
    label: "Fellowship", base: "dark",
    panel: { bg: "#15130f", panel: "#1c1a15", row: "#26231c", sel: "#3a3427",
             field: "#201d17", fg: "#e8dcc0", dim: "#9a8f76", line: "#2b271f",
             accent: "#c9a227" },
    term: { background: "#15130f", foreground: "#e0d4b8", cursor: "#c9a227",
            selectionBackground: "#3a3427",
            black: "#1c1a15", red: "#b5453c", green: "#7d8c4a", yellow: "#c9a227",
            blue: "#5a7a8c", magenta: "#96637f", cyan: "#6d9188", white: "#cdc1a4",
            brightBlack: "#5c5545", brightRed: "#e0685c", brightGreen: "#a8ba6a",
            brightYellow: "#eac54f", brightBlue: "#84a7bd", brightMagenta: "#c08fa8",
            brightCyan: "#95bdb2", brightWhite: "#f5ecd8" },
    art: {
      w: 16, h: 16,
      pal: { Y: "#c9a227", L: "#f0d27a" },
      rows: [
        "....YYYYYYYY....",
        "..YYYLLLLLLYYY..",
        ".YYLL......LLYY.",
        ".YLL........LLY.",
        "YYL..........LYY",
        "YL............LY",
        "YL............LY",
        "YL............LY",
        "YL............LY",
        "YL............LY",
        "YL............LY",
        "YYL..........LYY",
        ".YLL........LLY.",
        ".YYLL......LLYY.",
        "..YYYLLLLLLYYY..",
        "....YYYYYYYY....",
      ],
    },
  },

  drizzt: {
    label: "Drizzt", base: "dark",
    panel: { bg: "#0f0c18", panel: "#171226", row: "#211a33", sel: "#332a52",
             field: "#1a1430", fg: "#e6e1f5", dim: "#8f86b5", line: "#241d3a",
             accent: "#b39ddb" },
    term: { background: "#0f0c18", foreground: "#ded8f0", cursor: "#b39ddb",
            selectionBackground: "#332a52",
            black: "#171226", red: "#c8546b", green: "#5aa88b", yellow: "#c9a86a",
            blue: "#6f7fd0", magenta: "#a375d6", cyan: "#5fb0c9", white: "#c9c2e0",
            brightBlack: "#544a7a", brightRed: "#ef7a90", brightGreen: "#79d4ad",
            brightYellow: "#e8ca8c", brightBlue: "#93a3f5", brightMagenta: "#c79bf5",
            brightCyan: "#84d6ef", brightWhite: "#f2eefc" },
    art: {
      w: 18, h: 15,
      pal: { D: "#cfd6e4", W: "#e8e4f0", P: "#6b5e94", V: "#b39ddb",
             C: "#413a63", K: "#5a5180" },
      rows: [
        "..D............D..",
        "..D....WWWW....D..",
        "..D...WWWWWW...D..",
        "..D...WPPPPW...D..",
        "..D...WPVPVW...D..",
        "..D...WPPPPW...D..",
        ".DDD...PPPP...DDD.",
        "..K.....CC.....K..",
        "..K..CCCCCCCC..K..",
        "....CCCCCCCCCC....",
        "....CCCCCCCCCC....",
        ".....CCCCCCCC.....",
        ".....CCC..CCC.....",
        ".....KKK..KKK.....",
        "....KKKK..KKKK....",
      ],
    },
  },

  pacman: {
    label: "Pacman", base: "dark",
    panel: { bg: "#08080f", panel: "#101024", row: "#181838", sel: "#22225a",
             field: "#12122c", fg: "#f0f0d8", dim: "#8a8ab0", line: "#1c1c40",
             accent: "#ffd93b" },
    term: { background: "#08080f", foreground: "#f0e6c8", cursor: "#ffd93b",
            selectionBackground: "#22225a",
            black: "#101024", red: "#ff4d4d", green: "#5ad46a", yellow: "#ffd93b",
            blue: "#2b5df0", magenta: "#ff9ecb", cyan: "#5adcf0", white: "#e8e8d0",
            brightBlack: "#4a4a80", brightRed: "#ff7a7a", brightGreen: "#84f08f",
            brightYellow: "#ffe870", brightBlue: "#5f8bff", brightMagenta: "#ffb8dc",
            brightCyan: "#8aeaff", brightWhite: "#ffffff" },
    art: {
      w: 16, h: 9,
      pal: { Y: "#ffd93b", G: "#ff9ecb", E: "#dfe2f5", D: "#e8dcae" },
      rows: [
        "..YYYY.....GGG..",
        ".YYYYYY...GGGGG.",
        "YYYYY.....GEGEG.",
        "YYY.......GEGEG.",
        "YY...D.D..GGGGG.",
        "YYY.......GGGGG.",
        "YYYYY.....GGGGG.",
        ".YYYYYY...GGGGG.",
        "..YYYY....G.G.G.",
      ],
    },
  },

  /* The one theme here whose palette is not a costume: the seven ANSI slots
   * that matter are the seven tetrominoes, which happen to be seven colours
   * chosen decades ago to be told apart at a glance under pressure. */
  tetris: {
    label: "Tetris", base: "dark",
    panel: { bg: "#0d1226", panel: "#141b36", row: "#1d2749", sel: "#2a3766",
             field: "#17203f", fg: "#e6ecff", dim: "#8996c4", line: "#212c53",
             accent: "#31c7de" },
    term: { background: "#0d1226", foreground: "#dfe6ff", cursor: "#31c7de",
            selectionBackground: "#2a3766",
            black: "#141b36", red: "#e5504a", green: "#4fc26b", yellow: "#f2d03b",
            blue: "#3f7fe0", magenta: "#a558d0", cyan: "#31c7de", white: "#ccd6f0",
            brightBlack: "#4b5788", brightRed: "#ff7a72", brightGreen: "#74e08c",
            brightYellow: "#ffe66a", brightBlue: "#6fa5ff", brightMagenta: "#c684ef",
            brightCyan: "#6ce4f5", brightWhite: "#f2f6ff" },
    /* Deliberately coarse: a tetromino is a four-cell shape and drawing it at
     * fourteen cells wide would only make the cells smaller, not the pieces
     * clearer. Seven across means the blocks land about the size they were. */
    art: {
      w: 9, h: 7,
      pal: { I: "#31c7de", T: "#a558d0", S: "#4fc26b", O: "#f2d03b" },
      rows: [
        "..I......",
        "..I..OO..",
        "..I..OO..",
        "..I......",
        ".........",
        ".TTT..SS.",
        "..T..SS..",
      ],
    },
  },

  aincrad: {
    label: "Aincrad", base: "dark",
    panel: { bg: "#0a0d14", panel: "#111620", row: "#1a2130", sel: "#26334a",
             field: "#141a26", fg: "#e2e9f2", dim: "#8595a8", line: "#1d2534",
             accent: "#4fc3f7" },
    term: { background: "#0a0d14", foreground: "#dce5f0", cursor: "#4fc3f7",
            selectionBackground: "#26334a",
            black: "#111620", red: "#e0505f", green: "#4fbf8b", yellow: "#e0b451",
            blue: "#4a8fe0", magenta: "#9d7ae0", cyan: "#4fc3f7", white: "#c4d0de",
            brightBlack: "#4b5a6e", brightRed: "#ff707d", brightGreen: "#6fdfa8",
            brightYellow: "#ffd479", brightBlue: "#74b1ff", brightMagenta: "#bfa0ff",
            brightCyan: "#8adcff", brightWhite: "#f0f6ff" },
    art: {
      w: 18, h: 15,
      pal: { D: "#e8eef6", B: "#4fc3f7", H: "#3d4356", S: "#f2cdb0",
             E: "#6b7280", C: "#343a4e", T: "#4fc3f7", K: "#343b4e" },
      rows: [
        "..D............B..",
        "..D....HHHH....B..",
        "..D...HHHHHH...B..",
        "..D...HSSSSH...B..",
        "..D...HSEESH...B..",
        "..D...HSSSSH...B..",
        ".DDD...SSSS...BBB.",
        "..K.CCCCCCCCCC.K..",
        "..K.CCCCTTCCCC.K..",
        "...CCCCCTTCCCCC...",
        "...CCCCCTTCCCCC...",
        "....CCCCCCCCCC....",
        ".....CCC..CCC.....",
        ".....CCC..CCC.....",
        "....KKKK..KKKK....",
      ],
    },
  },
};
