# Session — Fork: arrow-key TUI selector over ALL Ollama models

**Timestamp:** 2026-06-12T135237Z
**Machine:** worlock
**Fork:** `fork_all-models-tui_2026-06-12T135237Z/` (gitignored, on-disk reference)

## Goal

Replace the `-s` / `-c` interactive model selector — a numbered `input()` menu
over the hand-curated `KNOWN_GOOD_MODELS` / `KNOWN_GOOD_CODE_MODELS` lists — with
a full-screen **curses TUI** that lists **every model Ollama has locally**
(`/api/tags`) and lets the user scroll ↑/↓ and press Enter to pick.

Created as a fork per repo convention (`.gitignore: fork_*/`; documented in the
README Forks Table), leaving the root `paper_processor.py` untouched.

## Changes (in the fork's `paper_processor.py`)

1. **`list_ollama_models()`** — new helper near `check_required_models`. GETs
   `/api/tags`, returns `[(name, "~N.N GB"), …]` name-sorted. Verified count
   matches `ollama list` (44 == 44).
2. **`prompt_model_selection_tui(models, title, default_name)`** — new curses
   picker beside `prompt_model_selection`:
   - Guards: returns `None` if not a TTY or `curses` import/init fails → caller
     falls back to the numbered menu.
   - Scrolling window (`idx` + `top` offset, `getmaxyx`-aware, rows truncated to
     width), cursor row in `A_REVERSE`, header + `↑/↓ move · Enter select · q cancel`
     footer.
   - Keys: ↑/↓ + vim `k`/`j`, Home/End, PgUp/PgDn, Enter select, `q`/Esc cancel.
   - **Robust arrow handling:** keypad translation is unreliable across terminals
     (a pty test showed arrows arriving as raw `ESC [ B`), so on `ESC` the code
     peeks the next bytes non-blocking and maps `[A`/`[B`/`[H`/`[F`/`[5~`/`[6~`
     itself; a bare `ESC` (no follow-on bytes) cancels. Works whether or not
     keypad mode fires.
3. **`_choose_model(default_name, curated)`** in `main()` — tries the TUI over
   all models, falls back to the curated numbered menu. Wired into both `-s`
   (`MODEL_TIERS["xl_quality"]` default) and `-c` (`CODE_MODEL` default).
4. **Docs:** updated the `--help` epilog + `-s` help string; the fork bundles a
   copy of `ocr_fallback.py` so it runs standalone from its own directory.

## Verification

- `ast.parse` syntax OK.
- `list_ollama_models()` count == `ollama list` (44).
- pty-driven navigation (isolated per case):
  - `down,Enter` → 2nd item ✓
  - `j,j,j,Enter` → 4th item ✓
  - bare `ESC` → `None` (cancel) ✓
  - `q` → `None` (cancel) ✓
- Non-TTY path returns `None` from the TUI → numbered fallback (guard verified).

## Commit

Fork dir is gitignored (on-disk only). Committed to lobster-graph: `README.md`
Forks Table row + this session doc.
