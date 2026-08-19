# Prompt for gathering feature ideas

Paste the block below into any LLM. It is written to get back a *usable
shortlist*, not a brochure: it names the product, states what already exists so
you do not get told to build it again, and puts the hard constraints in so the
answers stay buildable.

---

You are helping me plan features for a tool I am actively building. Be
concrete and opinionated. I want a prioritised list I can act on, not a survey.

**The tool.** A self-hosted web app that runs AI coding CLIs (Claude Code,
Gemini CLI, Grok, Codex, Aider and similar) in tmux sessions on my own server,
and gives me a browser UI to drive them: a folder-organised sidebar of
sessions, tabs across the top, a live terminal in the middle, and a prompt box
at the bottom. Sessions survive closing the laptop. I reach it privately over
Tailscale. It is one person's tool, not a SaaS.

**Who uses it.** Two audiences, and a good feature serves both:

- *Vibe coders* — people who direct AI agents more than they write code. They
  care about not losing their place, knowing which agent needs them, reusing
  prompts, and the thing feeling calm rather than technical.
- *Working developers* — people who also read diffs, run tests and ship. They
  care about speed, keyboard control, not fighting the tool, and being able to
  see what actually happened.

**What already exists** (do not propose these): folder tree with drag-and-drop,
rename, search, archive, folder colours; numbered tabs with keyboard switching;
live terminal with scrollback that survives reattach; per-CLI icons and
colours; auto-detection of which CLIs are installed; a prompt box with a
Run/Shell split and a repeat counter; text expanders/snippets; preset themes
plus custom CSS; independent font sizes; CPU/memory/disk/swap/load readout with
history; tab flashing and an optional chime when a session goes quiet; password
login plus API tokens so agents can drive it.

**Constraints — a suggestion that breaks one of these is not useful:**

1. No database, no build step, no heavy dependencies. Server is Python standard
   library; frontend is plain JavaScript.
2. It must stay agnostic about which CLI is running. Anything that only works
   for one vendor has to degrade gracefully for the others.
3. The box is small (4 CPUs, 16 GB) and each AI CLI already uses ~500 MB, so
   the tool itself must stay near-invisible in resource terms.
4. Not building: multi-user accounts, cloud hosting, mobile-specific UI,
   autonomous overnight agent orchestration, subagent visualisation.

**What I want back:**

1. **Twelve to twenty features**, each one line of what it is plus one line of
   why it matters, grouped as: *daily friction*, *awareness*, *organisation*,
   *safety*, *delight*.
2. For each, mark **who it is for** — vibe coder, developer, or both.
3. For each, mark **effort** — hours, a day, or more than a day — assuming a
   competent developer and the constraints above.
4. **Rank the top five** and say plainly why those five beat the rest.
5. **Name three things that sound good but are traps** here — features that
   look attractive and would be regretted, with the reason.

Bias toward things that get used forty times a day over things that demo well
once. If a suggestion already exists in a tool like VS Code, tmux, Warp or
Codeman, say which — I would rather copy a proven design than invent one.
