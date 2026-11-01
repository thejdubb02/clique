---
name: clique-release-check
description: Verify and ship CLIque work: visual_check.py after any web/ change, version bump and changelog stamping, shipped_check.py before calling a session done, cutting a release. Load when finishing a session, touching web/, or releasing.
---

# Look, release, verify

### Looking at it

Every other check here reasons about the code, and none of them can see that a
control is invisible, that one thing is covering another, or that a theme did
not reach the pane. That is where the bugs have actually been: a preview popup
layered above the context menu broke right-click on every sidebar row and the
suite stayed green through it.

```bash
~/.cache/clique-visual/bin/python tools/visual_check.py     # asserts, and writes screenshots
```

**Run it after any change to `web/`.** Setup is in the file's docstring — it
needs Chromium and Playwright, so it lives in a virtualenv of its own and the
product stays standard-library with no build step. Screenshots land in
`/tmp/clique-visual/`; open them, do not just read the passes.

### Releasing

Bump `__version__`, write the `CHANGELOG.md` entry, then run
`python3 tools/stamp_changelog.py` — it puts the wall-clock Pacific time on any
heading that lacks one, taking it from the release commit where there is one.
Skipping it leaves an entry the app renders without a time, and on a day with
ten releases the date alone distinguishes nothing. It is idempotent; run it
whenever.

### Before calling a session done

```bash
python3 tools/shipped_check.py
```

Four surfaces have to agree: the panel's version, the README badge, what
useclique.dev tells people to install, and what PyPI actually serves. They
drift apart quietly and each one embarrasses differently, and none of it is
visible from inside the panel. Ten seconds, so it is a command rather than a
habit.

Being ahead of PyPI is normal while work is in flight and stops being normal
the moment the site tells a stranger to `pip install`, which it does. Cutting
the release is: bump, changelog, tag `vX.Y.Z`, `gh release create`. Publishing
fires on the release, uses Trusted Publishing, and needs no token.
