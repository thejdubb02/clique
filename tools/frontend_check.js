/* Pure front-end logic, tested without a browser.
 *
 * app.js has no build step and no module system, which is the point — and it
 * also means the functions in it have nowhere to be tested from. Most of the
 * file is DOM work that only makes sense in a page, but the decisions are not:
 * what needs you first, and what counts as unread, are sorts over plain data,
 * and they are exactly the kind of thing that breaks quietly.
 *
 * So: read the file, cut out the region under test, eval it against stubs for
 * the handful of things it reads. No jsdom, no bundler, no package.json — the
 * same bargain the rest of the project makes.
 *
 *     node tools/frontend_check.js
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "clique/web/app.js"), "utf8");

let passed = 0;
let failed = 0;

function check(label, cond, detail) {
  if (cond) { passed++; console.log(`  ok   ${label}`); }
  else { failed++; console.log(`  FAIL ${label} ${detail === undefined ? "" : detail}`); }
}

/* A named region of app.js, so a test names the code it covers rather than a
 * line number that moves the next time anything is inserted above it. */
function region(from, to) {
  const start = SRC.indexOf(from);
  const end = SRC.indexOf(to);
  if (start < 0 || end < 0 || end <= start) {
    console.log(`  FAIL could not find the region ${from} … ${to}`);
    failed++;
    return "";
  }
  return SRC.slice(start, end);
}

console.log("what needs you first");
{
  const code = region("const NEXT_RANK", "/* Long press, because touch");

  let state;
  let activeId = null;
  const document = { hasFocus: () => true };
  const workState = (s) => {
    if (!s.alive) return "stopped";
    if (s.signal === "error") return "error";
    if (s.signal === "waiting") return "asking";
    if (s.busy) return "working";
    return "idle";
  };
  const unread = (s) => Boolean(s.alive && s.id !== activeId && s.activity > (s.last_seen || 0));
  const ago = () => "11m";
  void document; void workState; void unread; void ago;

  // `eval` of a block that declares functions puts them in this scope, so the
  // region is evaluated and then referenced — declaring a holder first would
  // collide with the declaration inside the code being tested.
  eval(code);

  const now = Math.floor(Date.now() / 1000);
  state = { sessions: [
    { id: "a", name: "fresh-error",   alive: 1, signal: "error",   activity: now - 5,   last_seen: now },
    { id: "b", name: "old-waiting",   alive: 1, signal: "waiting", activity: now - 660, last_seen: now },
    { id: "c", name: "new-waiting",   alive: 1, signal: "waiting", activity: now - 10,  last_seen: now },
    { id: "d", name: "unread-only",   alive: 1, signal: "",        activity: now - 300, last_seen: now - 900 },
    { id: "e", name: "busy",          alive: 1, signal: "", busy: 1, activity: now,     last_seen: 0 },
    { id: "f", name: "idle-and-read", alive: 1, signal: "",        activity: now - 90,  last_seen: now },
    { id: "g", name: "dead",          alive: 0, signal: "error",   activity: now - 30,  last_seen: now },
    { id: "h", name: "archived",      alive: 1, archived: 1, signal: "waiting", activity: now - 60, last_seen: now },
  ]};

  const got = nextUp();
  const named = (n) => got.findIndex((r) => r.s.name === n);
  const has = (n) => named(n) >= 0;

  // Stopped badly outranks blocked, even when the blocked one has been blocked
  // for eleven minutes: an error is not going to resolve itself.
  check("an error outranks a much older waiting", got[0] && got[0].s.name === "fresh-error",
        got.map((r) => r.s.name).join(","));
  check("among equals, the one blocked longest comes first",
        named("old-waiting") < named("new-waiting"));
  check("unread ranks below both", named("unread-only") === got.length - 1);

  // The list has to be short to be worth reading, so everything that does not
  // need a person is absent rather than ranked low.
  check("a working session never appears", !has("busy"));
  check("nor one that is idle and already read", !has("idle-and-read"));
  check("nor a dead one", !has("dead"));
  check("nor an archived one", !has("archived"));

  activeId = "b";
  check("nor the one you are already looking at", !nextUp().some((r) => r.s.id === "b"));
}

console.log("directories the panel already knows");
{
  const code = region("const CWD_SUGGESTIONS", "function openModal()");

  let state;
  let resumable;
  const shortPath = (p) => p;
  void shortPath;

  eval(code);

  const now = Math.floor(Date.now() / 1000);
  state = { sessions: [
    { cwd: "/srv/old",   alive: 0, last_seen: now - 10 },
    { cwd: "/srv/live",  alive: 1, last_seen: now - 5000 },
    { cwd: "/srv/dupe",  alive: 0, last_seen: now - 900 },
    { cwd: "/srv/dupe",  alive: 1, last_seen: now - 9000 },
    { cwd: "/srv/gone",  alive: 0, last_seen: now, archived: 1 },
    { cwd: "",           alive: 1, last_seen: now },
  ]};
  resumable = [{ cwd: "/srv/history", updated: now }, { cwd: "/srv/live", updated: now }];

  const dirs = knownDirs().map((d) => d.cwd);
  const kindOf = (cwd) => (knownDirs().find((d) => d.cwd === cwd) || {}).kind;
  // A live session is where you are working, so it beats a more recent look at
  // something that has since stopped.
  check("a running directory outranks a recently-viewed dead one",
        dirs.indexOf("/srv/live") < dirs.indexOf("/srv/old"), dirs.join(","));
  check("a directory appears once however many sessions are in it",
        dirs.filter((d) => d === "/srv/dupe").length === 1, dirs.join(","));
  check("and it is ranked by its best session, not its worst",
        dirs.indexOf("/srv/dupe") < dirs.indexOf("/srv/old"), dirs.join(","));
  check("past conversations are offered too", dirs.includes("/srv/history"));
  check("but rank below anything with a session", dirs.indexOf("/srv/history") > 0, dirs.join(","));
  check("archived is not somewhere you are working", !dirs.includes("/srv/gone"));
  check("and an empty path is never offered", !dirs.includes(""));

  // The groups the picker draws. A directory with a live session is "running
  // now" whatever else it also is, which is why the kind travels with the
  // winning score rather than being decided afterwards.
  check("a live session is grouped as running", kindOf("/srv/live") === "running");
  check("a stopped one as recent", kindOf("/srv/old") === "recent");
  check("and one only history knows about as history",
        kindOf("/srv/history") === "history");
  check("a directory with both is running, not recent", kindOf("/srv/dupe") === "running");
}

console.log("paths a pane printed");
{
  const code = region("const LINK_RE", "function openLink");
  // const in eval is trapped in the eval; a Function returns the bindings.
  const { PATH_RE, trimPath } = new Function(code + "; return { PATH_RE, trimPath };")();
  check("strips a compiler suffix", trimPath("src/app.js:42:7") === "src/app.js");
  check("strips a trailing period", trimPath("docs/foo.md.") === "docs/foo.md");
  const paths = (text) => {
    const out = [];
    PATH_RE.lastIndex = 0;
    let match;
    while ((match = PATH_RE.exec(text)) !== null) out.push(trimPath(match[1]));
    return out;
  };
  check("an absolute path", paths("see /tmp/foo.md")[0] === "/tmp/foo.md");
  check("a home path", paths("open ~/src/app.js")[0] === "~/src/app.js");
  check("a relative path with a slash and an extension",
        paths("wrote docs/foo.md")[0] === "docs/foo.md");
  check("dot-slash", paths("./src/app.js")[0] === "./src/app.js");
  check("a bare word is not a path", paths("hello world").length === 0);
  check("and a URL is not a path",
        paths("https://example.com/docs/foo.md").length === 0);
}

console.log("a login link that wrapped is still one link");
{
  const code = region("function paneRowsText", "function openLink");
  const {
    paneRowsText, paneUrlSegments, urlNeedsLocalCallback,
    paneHostIsRemote, tidyCopiedLink,
  } = new Function(code +
    "; return { paneRowsText, paneUrlSegments, urlNeedsLocalCallback, paneHostIsRemote, tidyCopiedLink };")();
  const parts = [
    { y: 8, text: "https://auth.openai.com/oauth/authorize?foo=1&code_challeng" },
    { y: 9, text: "e_method=S256&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback" },
  ];
  const joined = paneRowsText(parts);
  check("the two halves join without a gap",
        joined.indexOf("code_challenge_method") > 0);
  const segs = paneUrlSegments(parts, 0, joined.length);
  check("each row keeps its own slice",
        segs.length === 2 && segs[0].y === 8 && segs[1].y === 9, segs);
  check("a localhost callback is a remote-box login",
        urlNeedsLocalCallback(joined));
  check("a device-code page is not",
        !urlNeedsLocalCallback("https://auth.openai.com/codex/device"));
  check("opening this panel on the box itself is local",
        !paneHostIsRemote("127.0.0.1") && !paneHostIsRemote("localhost"));
  check("opening it over the tailnet is remote",
        paneHostIsRemote("box.tail1234.ts.net"));
  check("copying a wrapped URL drops the line break",
        tidyCopiedLink("https://auth.openai.com/codex/device\n/extra")
          === "https://auth.openai.com/codex/device/extra");
}

console.log("things that sit on top of other things");
{
  // A preview that outranks a menu is a menu nobody can use — and it fails
  // silently, because the click still lands on the control you cannot see.
  // The stack is small enough to assert outright.
  const css = fs.readFileSync(path.join(ROOT, "clique/web/app.css"), "utf8");
  const layerOf = (selector) => {
    const at = css.indexOf(selector + " {");
    if (at < 0) return null;
    const found = /z-index:\s*(\d+)/.exec(css.slice(at, css.indexOf("}", at)));
    return found ? Number(found[1]) : null;
  };
  const menu = layerOf("#menu");
  const palette = layerOf("#palette");
  const follow = layerOf("#follow");
  const file = layerOf("#file");
  check("the palette sits above the context menu", palette > menu, `${palette} vs ${menu}`);
  check("and the menu above the pane's own controls", menu > follow, `${menu} vs ${follow}`);
  check("the file sheet sits above the palette", file > palette, `${file} vs ${palette}`);
}

console.log("tinted greys keep their contrast");
{
  // The point of tinting the 256-colour greyscale ramp is that a monochrome
  // theme owns the shades a CLI paints with. The point of doing it by hue
  // rather than by interpolation is that an application picking 233 chose how
  // far from the background it wanted to be — that choice is not ours to move,
  // and moving it is what makes text stop being readable on top.
  global.window = {};
  require(path.join(ROOT, "clique/web/themes.js"));
  const CUBE = [0, 95, 135, 175, 215, 255];
  eval(region("function hexRgb", "const _termThemes").replace(/^const CUBE.*$/m, ""));

  const theme = global.window.CLIQUE_THEMES.trinity;
  const ramp = extendedAnsi(theme).slice(216);
  const lum = (hex) => {
    const p = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
    return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
  };

  check("the ramp still has 24 steps", ramp.length === 24, ramp.length);
  let held = true;
  let rising = true;
  for (let i = 0; i < 24; i++) {
    const v = 8 + 10 * i;
    const standard = "#" + [v, v, v].map((c) => c.toString(16).padStart(2, "0")).join("");
    if (Math.abs(lum(standard) - lum(ramp[i])) > 0.07) held = false;
    if (i && lum(ramp[i]) <= lum(ramp[i - 1])) rising = false;
  }
  check("every step keeps the lightness xterm would have given it", held, ramp.slice(0, 3));
  check("and the ramp still climbs from dark to light", rising);

  // The cube is not ours. An application asking for colour 82 wants that
  // green, not a theme's idea of one.
  const cube = extendedAnsi(theme).slice(0, 216);
  check("the 6x6x6 cube is left exactly as xterm defines it",
        cube[0] === "#000000" && cube[215] === "#ffffff", [cube[0], cube[215]]);

  const plain = global.window.CLIQUE_THEMES[""];
  check("a theme that did not ask keeps the standard ramp", !plain.tint_greys);
}

console.log("copy from a pane that is eating the mouse");
{
  const clickCode = region("function paneAtLiveScreen", "const PANE_DRAG_PX");
  const {
    paneAtLiveScreen, paneSgrClick, paneGridCell,
  } = new Function(clickCode +
    "; return { paneAtLiveScreen, paneSgrClick, paneGridCell };")();
  check("a click at the live screen is a click",
        paneAtLiveScreen({ buffer: { active: { viewportY: 10, baseY: 10 } } }));
  check("a click on older scrollback is not",
        !paneAtLiveScreen({ buffer: { active: { viewportY: 2, baseY: 10 } } }));
  check("the click report is SGR press then release",
        paneSgrClick(12, 4) === "\x1b[<0;12;4M\x1b[<0;12;4m");
  const cell = paneGridCell(
    { element: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 40 }) },
      cols: 10, rows: 4 },
    50, 10);
  check("a click in the middle maps to a 1-based cell",
        cell.col === 6 && cell.row === 2, cell);

  const code = region("const PANE_DRAG_PX", "function wirePaneClipboard");
  const {
    paneForceSelectMods, paneDragFarEnough, paneShouldStealMouse,
  } = new Function(code +
    "; return { paneForceSelectMods, paneDragFarEnough, paneShouldStealMouse };")();
  const click = { button: 0, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false };
  check("a Mac uses Option, not Shift, to force a select",
        paneForceSelectMods("MacIntel").altKey && !paneForceSelectMods("MacIntel").shiftKey);
  check("everything else uses Shift",
        paneForceSelectMods("Linux x86_64").shiftKey && !paneForceSelectMods("Linux x86_64").altKey);
  check("five pixels is a drag", paneDragFarEnough(0, 0, 5, 0));
  check("four is not", !paneDragFarEnough(0, 0, 3, 3));
  check("a drag is stolen when the CLI is eating the mouse",
        paneShouldStealMouse(click, true, true));
  check("a click with a modifier is left alone",
        !paneShouldStealMouse({ ...click, shiftKey: true }, true, true));
  check("and so is Ctrl-click, which drops a path",
        !paneShouldStealMouse({ ...click, ctrlKey: true }, true, true));
  check("a phone is not stolen from — the Copy chip is the way",
        !paneShouldStealMouse(click, true, false));
  check("and a shell with no mouse tracking selects on its own",
        !paneShouldStealMouse(click, false, true));
  check("a touch-synthesised mouse is left alone",
        !paneShouldStealMouse({ ...click, sourceCapabilities: { firesTouchEvents: true } },
                              true, true));
}

console.log("zoom a boxed pane instead of wrapping it");
{
  const code = region("const PANE_ZOOM_MIN", "function paneForceSelectMods");
  const {
    paneWidthScale, paneShouldZoom, paneQueueOut, PANE_ZOOM_MIN,
  } = new Function(code +
    "; return { paneWidthScale, paneShouldZoom, paneQueueOut, PANE_ZOOM_MIN };")();
  check("room to spare is no zoom",
        paneWidthScale(800, 80, 8) === 1);
  check("half the width zooms to half",
        Math.abs(paneWidthScale(320, 80, 8) - 0.5) < 0.01);
  check("a boxed CLI at half size zooms",
        paneShouldZoom(true, 0.5));
  check("a shell never zooms",
        !paneShouldZoom(false, 0.5));
  check("a phone-sized shrink still resizes for real",
        !paneShouldZoom(true, PANE_ZOOM_MIN - 0.01));
  const held = paneQueueOut([], "hi", 8);
  check("keys typed while reconnecting are kept",
        held.join("") === "hi");
  const capped = paneQueueOut(["aaaaaaaa"], "bbbb", 8);
  check("and an 8k flood cannot pile up forever",
        capped.join("").length <= 8 && capped.join("").endsWith("bbbb"));
}

console.log("what's new");
{
  const code = region("function baseVersion", "let loadedVersion");
  const {
    baseVersion, changelogHasNews, CLOG_SHOW,
  } = new Function(code +
    "; return { baseVersion, changelogHasNews, CLOG_SHOW };")();
  check("the sheet holds five releases", CLOG_SHOW === 5);
  check("a build suffix is not a new release",
        baseVersion("0.50.24+abc") === "0.50.24");
  check("an unread upgrade is news",
        changelogHasNews("0.50.24", "0.50.23"));
  check("the same version is not",
        !changelogHasNews("0.50.24", "0.50.24"));
  check("and neither is a first look, before anything is stamped",
        !changelogHasNews("0.50.24", ""));
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
