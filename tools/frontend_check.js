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
  const code = region("const NEXT_RANK", "/* The last few lines of a session");

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

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
