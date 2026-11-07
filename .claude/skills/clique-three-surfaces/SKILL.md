---
name: clique-three-surfaces
description: CLIque ships as panel/PWA, Windows desktop and Android. Load at the end of any panel feature to decide whether Android needs a port and to record it in clique-android/docs/port-plan.md.
---

# Three surfaces

The panel is the product. Two clients wrap it, and a new feature does not reach
them on the same terms:

| | What it is | What a new panel feature costs it |
|---|---|---|
| **PWA** | the panel, installed | nothing, it is the same page |
| **Desktop** (`clique-desktop`) | a Windows window around the panel | nothing, it is a webview of the same page |
| **Android** (`clique-android`) | a native app with its own terminal | a port, every time |

So the question at the end of a feature is only ever about the phone, and it
gets asked out loud rather than assumed: **does Android need this, and if not,
why not?** A yes becomes a tiered row in `clique-android/docs/port-plan.md`, a
no goes in that file's Tier 3, which exists so leaving something out is a
decision on the record instead of an oversight.

Three releases, none implied by another: PyPI for the panel, our own F-Droid
repo for the phone, a GitHub release for the desktop exe.
