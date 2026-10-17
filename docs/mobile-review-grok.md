# Mobile review (Grok)

How well CLIque works on a real phone: iOS Safari and Android Chrome, browser tab and installed PWA. Read from the code only. Not run, not measured on a handset.

Skipped, already in `docs/next.md`: the 13px sideways scroll, the key-row keys being narrower than 44px, QR login, local echo, and the planned pass on the tab bar, input-bar spacing, and swipe-between-sessions.

Worst first. Each item is something a person on a phone would fail to do, with a line that says so.

---

## 1. Opening a session pops the keyboard over the prompt, then types into the terminal

This is the one that makes the product feel broken the first time you try to talk to an agent from the sofa.

`promptWanted()` forces the panel's textarea on a phone so Android Gboard cannot spam the pane (`clique/web/app.js:4114-4129`). Then every session open focuses the terminal instead:

- `showActivePane()` always calls `entry.term.focus()` (`clique/web/app.js:5937-5942`).
- `openSession()` calls that, then `landFocus()` (`clique/web/app.js:5995-6000`).
- `landFocus()` refuses to focus `#prompt` on a coarse pointer, on purpose, so the keyboard does not jump up (`clique/web/app.js:9256-9263`).

The keyboard still jumps up, because xterm's hidden textarea just got focus, inside a tap handler. On iOS that is enough. On Android it is the IME path the comment at `4116-4123` already says fills the pane with the same sentence over and over.

Nothing then shrinks the layout around that keyboard:

- `body` and `#shell` are `height: 100dvh; overflow: hidden` (`clique/web/app.css:46-56`). `dvh` tracks the URL bar, not the keyboard.
- The only size listener is `resize` (`clique/web/app.js:8658`). There is no `visualViewport` listener anywhere in `clique/web/`.
- The panel viewport tag is `width=device-width, initial-scale=1` (`clique/web/index.html:4`). No `interactive-widget=resizes-content`, so current Chromium keeps the overlay-keyboard behaviour too.

The prompt, the key row, Run, and the status bar live at the bottom of that frozen `100dvh`. The keyboard covers them, and `overflow: hidden` means you cannot scroll them into view. Keys go into the terminal you were trying not to type in. Tapping Esc/Tab/^C on the key row calls `focusTerminal()` again (`clique/web/app.js:10286-10294`), so even if you had fought focus onto the prompt, one special key puts you back in the IME.

What you fail to do: type a prompt and press Run, on the first session you open, without the keyboard eating the box and (on Android) duplicating the line into the pane.

---

## 2. The installed app draws under the notch and the home indicator

The sign-in page sets `viewport-fit=cover` (`clique/auth.py:150`). The panel does not (`clique/web/index.html:4`). iOS Add to Home Screen then loads the panel, with:

```
apple-mobile-web-app-status-bar-style = black-translucent
```

(`clique/web/index.html:26-27`). Translucent means the web content starts at y=0, under the status bar and the Dynamic Island.

The standalone rule that is supposed to pad the safe area:

```
@media (display-mode: standalone), (display-mode: window-controls-overlay) {
  body { padding: env(safe-area-inset-*); }
}
```

(`clique/web/app.css:1297-1301`) does nothing without `viewport-fit=cover`, because those `env()` values are 0. Even if they were not, the overlay chrome ignores body padding: the sidebar drawer is `position: fixed; top: 0; bottom: 0` (`clique/web/app.css:1794-1800`), the side panel is `inset: 35px 38px 0 0` (`clique/web/app.css:2413-2416`), and the toast sits at `bottom: 18px` (`clique/web/app.css:724-725`). Home indicator is ~34px. Undo after a kill lives in that toast.

In a Safari tab the browser chrome mostly hides this. As an installed app on a notched iPhone: the tab strip and the sidebar header sit under the island, so you cannot tap the first row of controls, and the toast's Undo is under the home bar.

The login page itself is fine on this point. The app you open after signing in is not.

---

## 3. The session long-press menu does not scroll, so Kill hangs off the screen

Long-press on a sidebar row is the phone's right-click, and it is wired (`clique/web/app.js:2545-2616`). The menu it opens is not.

`#menu` is a fixed box with no `max-height` and no `overflow` (`clique/web/app.css:824-833`). Coarse pointers get `padding: 11px 16px` per row (`clique/web/app.css:1521-1523`). A live session's menu is 15 to 20 items (`clique/web/app.js:2446-2479`), about 40px each, 600 to 800px tall.

Placement is:

```
top = min(clientY, innerHeight - menu.offsetHeight - 8)
```

(`clique/web/app.js:2438-2439`). When the menu is taller than the screen that value goes negative, so Open is clipped at the top and Kill, Interrupt, Archive sit below the bottom. There is nothing to scroll.

On an iPhone SE (667px) you cannot kill, archive, or lock a session from the menu that exists specifically so a phone can do those things. A taller iPhone in a Safari tab is close enough that the last rows are still a thumb's reach past the home indicator.

Working groups do not even get that menu: `wireTouchMenus()` only listens on `#tree` (`clique/web/app.js:2561-2573`). Group rows live in `#groups` and only have `oncontextmenu` (`clique/web/app.js:398-424`). Open is a visible button. Rename and delete are right-click only. On a phone you can launch a group and you cannot rename or remove it.

---

## 4. There is no way to give a session a photo or a file from the phone

Paste of a clipboard image is handled (`clique/web/app.js:8501-8523`). Drop of a file is handled (`clique/web/app.js:8525-8567`). There is no `<input type="file">` anywhere in the panel or the sign-in page.

A phone has no drag-and-drop. iOS will sometimes paste a screenshot you just took. It will not let you pick a photo from the library, a PDF from Files, or a log from another app, because nothing in the UI asks the OS for a file.

What you fail to do: the thing the Images settings copy describes as "Drop a file anywhere on the window." From a phone, that path does not exist.

Copying text out of the pane is in the same family. The host is `touch-action: none` (`clique/web/app.js:6166-6174`) so a finger drag is captured as our scroll (`clique/web/app.js:9680-9750`), and a long-press on the pane opens the file menu (`clique/web/app.js:2618-2652`, `8439-8467`). There is no gesture left that creates an xterm selection, so `#selChips` Copy never appears. You cannot copy an error off the screen. You can long-press a path and copy that path.

---

## 5. The 44px hit padding overlaps the next control, and the right 12px of the pane

The 0.64.0 pass grew hit areas with `::after { inset: -16px }` on header buttons, tab close/gear, the right rail, and the rest (`clique/web/app.css:2467-2478`). The drawing stayed 32px. The target became 64px.

Sidebar header buttons sit with `gap: 2px` (`clique/web/app.css:91-95`). Two 64px targets 34px apart overlap by about 30px. The later button's `::after` paints on top, so a tap on the right half of More hits Settings, and a tap on the right half of Settings hits Collapse. Collapse hides the drawer you just opened to reach those buttons.

The right rail is 38px (`clique/web/app.css:2232-2236`) with 30px buttons and the same -16px after. That after sticks ~12px into the terminal. A tap on the last columns of the pane opens Notes, Git, Info, or Export instead of the TUI under the finger.

This is not "targets still small." The visual check counted size. Overlap is what a thumb actually hits.

---

## 6. Turn the phone sideways and the phone layout goes away

`isMobile()` and the drawer/key-row CSS all key off `max-width: 640px` (`clique/web/app.js:10211`, `clique/web/app.css:1794-1807`). `handheld()` keys off `pointer: coarse` (`clique/web/app.js:10064-10066`).

An iPhone in landscape is 667 to 932px wide and still a coarse pointer. So is an iPad. The overlay drawer does not apply, the on-screen Esc/Tab/^C/arrows row hides, the resizer comes back (a drag handle on a screen you cannot drag with a mouse), and there is no scrim. You rotate to get more of the pane and lose the keys a soft keyboard cannot send.

Portrait below 640px is the layout that was actually built for a phone. Landscape is the desktop layout with a thumb.

---

## 7. The prompt capitalises and autocorrects

`#prompt` is a plain textarea (`clique/web/index.html:218`). No `autocapitalize="none"`, no `autocorrect="off"`, no `spellcheck="false"`, no `enterkeyhint`.

iOS default for a textarea is sentences. The first word of a command becomes `Cd`, `Git`, `Docker`. Autocorrect turns a cwd or a flag into an English word, then Run sends that. The whole reason the box exists on a phone is to compose a line in a real field. The field is still a prose box.

Notes have the matching miss for iOS zoom. The 16px floor applies to `input, textarea, select` (`clique/web/app.css:2438-2445`). Note lines are `contenteditable` at 13px (`clique/web/app.js:3156-3158`, `clique/web/app.css:2329-2332`). Tap a note on an iPhone and Safari zooms the page. `body` is `overflow: hidden` and the pane is `touch-action: none`, so pinching back out has to happen on the chrome, not on the thing you zoomed.

The note checkbox is 15px and the send/remind/add/delete buttons are 22px (`clique/web/app.css:2319-2342`). They appear on `focus-within` after you tap the line, which does work, but the four 22px icons sit in a 1px gap. Delete is the one you hit when you meant remind.

---

## What is fine

These are doing the job the comments say they are.

- `100dvh` after `100vh` (`clique/web/app.css:48-56`). The collapsing Safari URL bar is handled. The keyboard is the hole, not this.
- Inputs, textareas and selects are floored at 16px on a coarse pointer (`clique/web/app.css:2438-2445`). iOS will not zoom those. Contenteditable was the miss.
- Long-press on session rows, folder heads, and history rows in `#tree` (`clique/web/app.js:2545-2616`, `2132-2143`). That part of "no right-click" is actually closed.
- Folder pencil and tab close/gear stay visible on coarse pointers (`clique/web/app.css:1524`, `1727-1732`). Hover is gated. A tap does not leave a sticky highlight.
- Move-to-folder is in the session menu, not drag-only (`clique/web/app.js:2464-2470`). Draft-move has a tap as well as a drag (`clique/web/app.js:8328-8338`).
- A phone starts with the drawer shut, and opening a session closes it (`clique/web/app.js:10331-10333`, `5972-5973`).
- Handheld wins the shared tmux window, and `hold` / `release` fire on hide and pagehide (`clique/web/app.js:10030-10099`). Two windows will not fight while the phone is in your hand. That resize fight is the one that is actually fixed.
- Sign-in: 16px type, 44px targets, `viewport-fit=cover`, install tags, service worker (`clique/auth.py:150-190`, `221-228`). First paint of the password page is the one surface that was built for a thumb.
- Settings tabs wrap (`clique/web/app.css:1188-1193`). About is not off a hidden scrollbar.
- Status bar drops readings with `@container` queries (`clique/web/app.css:479-488`, `2455-2460`). The 460px-in-393px clip is gone.
- Prompt Enter ignores an IME commit (`clique/web/app.js:8358-8363`).
- Reload shows only when there is no address bar (`clique/web/app.js:8108-8114`). Theme art and the CLI watermark hide on a narrow pane (`clique/web/app.css:555-558`).
- Manifest `display: standalone`, a no-cache service worker, and `navigator.standalone` are enough for both stores to offer install (`clique/web/manifest.webmanifest`, `clique/web/sw.js`, `clique/web/app.js:10376-10380`). Install itself is fine. What you land in is item 2.

---

A short honest list: typing is the hole, then the installed chrome, then the menus that exist so a phone can reach desktop-only actions. The 0.64.0 pass fixed zoom-on-focus for form controls, the overflowing status bar, and target *size*. It did not fix the keyboard covering the box you type in, and the hit-area padding it added now overlaps.
