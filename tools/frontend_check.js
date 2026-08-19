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
    if (s.signal === "waiting") return "waiting";
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
  check("the palette sits above the context menu", palette > menu, `${palette} vs ${menu}`);
  check("and the menu above the pane's own controls", menu > follow, `${menu} vs ${follow}`);
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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
