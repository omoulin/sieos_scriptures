# Scriptures

Read the scriptures from churchofjesuschrist.org offline: footnotes, full-text search,
highlights, notes and tags, several chapters open at once in tabs, and notes synced with a
Church Account (Gospel Library).

Written in C and drawn the way the Atlas desktop draws (`../sieos_atlaswm`: `draw.c`, `look.h`),
in its glass look as one of its apps (atlas-files) shows it: light panels, a white page, dark text,
one amber accent (a gloss), DejaVu Sans for text and DejaVu Sans Mono for numbers. At night the
panels are smoked and the text light. The app follows Atlas's look file (`~/.config/atlas/look`):
its size of things (`zoom=`, 50 to 400%, or `$ATLAS_ZOOM`; the app's own size in ⚙ multiplies it)
and its palette (`night=`: 0 day, 1 night, 2 auto, from 19:00 to 7:00; or `$ATLAS_LOOK_NIGHT`). No GNOME, GTK or WebKit: the app
draws every pixel inside its window; the window manager provides the title bar and the
minimize, maximize and close buttons.

## Build and run

```sh
make            # needs only a C compiler: SDL2's headers are vendored in third_party/
./build/scriptures
make install    # installs /usr/local/bin/scriptures (sudo if needed) and adds it to the app list
                # (PREFIX=... to change; ./install.sh --uninstall removes it)
```

At run time it uses the system's SDL2 library, the `curl` program (downloads, sync) and the
DejaVu fonts (Sans and Sans Mono); `libsqlite3` is optional (importing the earlier Python version's library, reading
Firefox's session). With `libsdl2-dev` installed, `make` uses pkg-config instead.

The library lives in `data/` next to `build/` (or `$SCRIPTURES_DATA`, else
`~/.local/share/scriptures`, which is what the installed program uses; `make install` copies
`data/` there the first time). On first start, a library downloaded by the earlier Python version
(`data/library.db`) is imported, chapters and notes alike, in about a second.

## Using it

- **Library** (top left, Ctrl+L): volume → book → chapter grid. Ctrl+click or middle-click
  opens a chapter, a search result, a note or a footnote link **in a new tab**.
- **Tabs**: Ctrl+T new, Ctrl+W close, Ctrl+Tab or Ctrl+PgDn / PgUp to switch, Ctrl+1…9.
  Open tabs come back at the next start, each at its place.
- **Reading**: the ‹ and › arrows at the page's sides (or ← →, Alt+↑ / Alt+↓) go to the previous
  and next chapter; ← Back (top left of the page, or Alt+←) returns where a link came from; ↑
  (bottom right, once scrolled) goes back to the top. Ctrl+ + / − sets the text size.
- **Footnotes**: click a footnote letter or word; links in the footnote open in the app.
- **Highlights and notes**: select text (or double-click a word) → a colour, ✎ Note, or Copy
  (with its reference). Click a highlight to edit its colour, title, tags and note.
- **All your notes** (Ctrl+Shift+N, or "Notes & Highlights" in the library): a tab with a card
  per note: the reference, the highlighted words, the title, the note, the tags and the date.
  Filter by words or by a tag; newest first or in book order. Click a card to edit it; its
  **Go to verse →** button (or its reference) shows the verse: in the tab already open on that
  chapter, else in a new tab (Ctrl+click: a new tab in the background).
- **Search**: Ctrl+F; every word must appear; accents and case are ignored.
- **⚙** (bottom left, Ctrl+,): Library & Account: the size of everything and of the reading text; the language and the volumes to
  download (the standard works are ~1,600 pages, ~5 minutes); the Church Account.

## Church Account

In **⚙ → Church Account**:

1. **Open the Sign-In Page** opens the Church's page in your default browser.
2. Sign in there as usual (two-step verification included) and stay on the notes page. Press
   F12, open the Network tab, type `annotations` in its Filter box and press F5. Click an
   `annotations?…` request; under Request Headers find `cookie` (Chromium browsers write it in
   lower case), right-click it → **Copy value**. (The page's first request often shows only
   "provisional headers", without the cookie: use an `annotations` request.)
3. **Paste & Connect** reads the clipboard, checks the session with the Church site, and saves it.

The app never sees your password. With Firefox as the default browser, it also notices your
sign-in by itself (from Firefox's cookie store) and offers **Use My Firefox Session**.

When a session expires during sync, the app opens these steps again. The session is stored in
`data/account` (mode 600): anyone with it can act as you on the Church website until it
expires; **Disconnect** removes it.

**Download Notes From Account** pulls highlights, notes and tags. **Two-Way Sync** also uploads
local changes; it is experimental (the notes service has no public API; the upload format is
reverse-engineered and has not been tried on a real account). Gospel Library places highlights
by word count and its apps count slightly differently, so one can be off by a word.

## Layout (and porting to SIEOS)

```
src/
  util json html store layout   portable core: data, parsing, files, text layout
  draw ui                       drawing (Atlas's model and palettes, day and night) and widgets
  library                       downloads and notes sync (over net.h)
  app panels                    the window: tabs, reader, sidebar, popovers, dialogs
  plat.h    plat_sdl.c          Linux: the window (SDL2)         SIEOS: an Atlas window
  font.h    font_tt.c           Linux: DejaVu via stb_truetype   SIEOS: Atlas's fonts
  net.h     net_curl.c          Linux: the curl program          SIEOS: its http.c
  linux_import.c sqlite_min.c   Linux only: the old library, Firefox's session
third_party/   SDL2 headers (2.32.4), stb_truetype.h
legacy/        the earlier Python versions (not used)
data/          your library and notes
```

The core is C11 with integers only in the drawing code, no GNU extensions, and a reentrant
tokenizer (no `strtok`). Storage is plain files (chapters as the API's JSON, a text search
index, `notes.json`), so it moves to siefs as is.

`./build/scriptures --script FILE DIR` runs a test script (synthetic input, PNG snapshots);
with `SDL_VIDEODRIVER=offscreen` it needs no display.

Scripture text and study helps © Intellectual Reserve, Inc., downloaded for personal study;
see the site's terms of use before redistributing them.
