# drovr

Fork of [AVGVSTVS96/herdr-drovr](https://github.com/AVGVSTVS96/herdr-drovr) with reliable destination focus and a separate workspace-name prompt.

A [drover](https://en.wikipedia.org/wiki/Drover_(Australian)) moves herds between places. **drovr** does the same for your [herdr](https://herdr.dev) panes and tabs: move the focused tab to another workspace, or the focused pane into any tab, from a fuzzy picker in a floating popup.

Herdr has no built-in "move tab to workspace", and recreating a tab by replaying its layout would respawn every pane, killing running agents and shells. drovr relocates the *live* panes instead (`herdr pane move`), so everything keeps running exactly where it left off.

<img width="1104" height="640" alt="drovr picker" src="https://github.com/user-attachments/assets/3f5fecde-85fd-4221-b994-e3c54d16305d" />

## Features

- 🚚 **Move tabs** -- label, split layout, and every running pane relocate to another workspace
- 📦 **Move panes** -- send the focused pane into any tab, split right or down, or into a fresh tab or workspace
- 🤖 **Live agents survive** -- panes are relocated, never respawned
- 🎈 **Floating picker** -- theme-matched fzf popup; the tiled layout never shifts
- 🌐 **Cross-workspace** -- `ctrl-t` in the pane picker opens every workspace's tabs as destinations
- 🎯 **Focus follows** -- the final move focuses the destination, even when Herdr closes the source tab and picker
- ✏️ **Name new workspaces** -- confirm an editable name in a separate prompt; the moved tab keeps its label
- ✅ **Verified moves** -- every move is checked against the server; failures surface right in the picker

## Requirements

- **herdr** `>= 0.7.4` -- the picker uses floating popup panels
- **node** `>= 23` -- runs the TypeScript sources directly via native type stripping; no build step
- **fzf** on `PATH` -- `brew install fzf` on macOS

## Install

```bash
herdr plugin install applifaction/herdr-drovr
```

The plugin ID remains `drovr`, so existing drovr keybindings work unchanged. Installing this fork replaces the installed drovr entry rather than adding a second plugin. To return to upstream, run `herdr plugin install AVGVSTVS96/herdr-drovr`.

Herdr keybindings live in your config, not the plugin manifest, so add these to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+M"
type = "plugin_action"
command = "drovr.move-tab"
description = "move tab to workspace"

[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "drovr.move-pane"
description = "move pane to tab"
```

Then reload:

```bash
herdr server reload-config
```

## Usage

**Move a tab** -- focus it, press `prefix+M`, pick a destination workspace.

**Move a pane** -- focus it, press `prefix+m`, pick a destination tab.

| key | action |
| --- | --- |
| type | filter destinations |
| `enter` | move (pane moves split **right**) |
| `alt-d` | move, splitting **down** (pane picker) |
| `ctrl-t` | toggle every workspace's tabs (pane picker) |
| `esc` | cancel |

The tab picker offers `＋ new workspace`; the pane picker also offers `＋ new tab`. These rows stay below the real matches, so `enter` still lands on the best real match.

- **New workspace:** selecting `＋ new workspace` opens a separate name prompt. The search query is an editable suggestion, not an automatic name. Enter a non-empty name and press `enter`; `esc` cancels without moving anything. Empty or whitespace-only names are rejected. The tab being moved keeps its original label.
- **New tab:** the typed search query still supplies its name, previewed live (`＋ new tab "api"`).

If a move leaves the source tab or workspace empty, Herdr closes it. Keyboard focus follows the moved tab or pane to the destination. For a multi-pane tab, focus lands on the last pane placed while reconstructing the layout.

## How it works

Two scripts, zero runtime dependencies:

- **`open-picker.ts`** runs headless behind the keybinding: it resolves the focused pane and opens the picker popup with the move context in its environment.
- **`pick-and-move.ts`** runs inside the popup, where fzf has a real TTY. A popup is a session resource, not a pane in the source tab, so nothing pins the source layout and the moves run inline right after the pick.

A tab move reconstructs the layout tree from the rects Herdr actually reported (no ratio arithmetic), re-validates the source after the picker and any name prompt, then replays the tree in the destination: one `pane move --new-tab`/`--new-workspace` for the anchor, then one `pane move --split` per split node with the exact direction and ratio. Earlier moves use `--no-focus`; the final move uses `--focus`. A pane move uses `--focus` in its single move request as well.

Focus must be part of the move itself: on Herdr 0.9.1, removing the last source pane also closes its tab and popup, terminating the picker before any subsequent focus command could run.

There is no atomic `tab move` API, so a tab move is several `pane move` calls. Sources are re-validated right before moving; if a move still fails partway, drovr reports the failing command and does not roll back.

## Development

```bash
git clone https://github.com/applifaction/herdr-drovr
herdr plugin link ./herdr-drovr
herdr plugin action list --plugin drovr

npm install   # dev-only: typescript for typechecking
npm test      # typecheck, layout/picker tests, and CLI-flow regression tests
```

Optional real-TUI regression tests require Herdr, fzf, Python 3 and `pyte`. They start a disposable Herdr server with isolated config, sockets and PTY; they never connect to your running session. Focus is checked by sending keyboard input through the actual client and identifying the shell that receives it.

```bash
cd herdr-drovr
python3 -m venv .local-validation/venv
.local-validation/venv/bin/pip install pyte
.local-validation/venv/bin/python tests/tui_smoke.py --output .local-validation/tui.json
.local-validation/venv/bin/python tests/tui_smoke.py --split --output .local-validation/tui-split.json
.local-validation/venv/bin/python tests/tui_smoke.py --destination new --blank-name --output .local-validation/tui-name.json
.local-validation/venv/bin/python tests/tui_smoke.py --destination new --cancel-name --output .local-validation/tui-cancel.json
.local-validation/venv/bin/python tests/tui_smoke.py --destination new --close-workspace --output .local-validation/tui-close.json
.local-validation/venv/bin/python tests/tui_smoke.py --mode pane --output .local-validation/tui-pane.json
.local-validation/venv/bin/python tests/tui_smoke.py --mode pane --destination new --output .local-validation/tui-pane-new.json
```

## License

[MIT](LICENSE)
