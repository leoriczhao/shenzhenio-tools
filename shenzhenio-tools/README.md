# shzio

External Python tooling for SHENZHEN I/O solution files.

See [ROADMAP.md](ROADMAP.md) for the complete execution plan, phase gates, and
current priorities.

Current scope:

- Parse and write solution `.txt` files.
- Model trace grids with the game's `Exists` and directional bit masks.
- Model parts, pins, boards, and typed nets.
- Build solutions through a Python API.
- Route typed nets deterministically on extracted board geometry.
- Run basic static checks before writing files back to the game save directory.
- Analyze physical trace nets from generated or existing save files.
- Extract board and terminal metadata from custom specification Lua files.

This is not a full simulator yet.

## Commands

Run from this directory with `PYTHONPATH=src` until the package is installed:

```powershell
$env:PYTHONPATH='src'
python -m shzio.cli extract-game --output .\data\raw\game-metadata.json
python -m shzio.cli decode-game-strings
python -m shzio.cli extract-chip-catalog
python -m shzio.cli extract-puzzle-catalog
python -m shzio.cli extract-board-catalog
python -m shzio.cli compare-board-catalog Sz035
python -m shzio.cli check .\solutions\virtual_reality_buzzer.py
python -m shzio.cli build .\solutions\virtual_reality_buzzer.py -o .\build\virtual-reality-buzzer-2.txt
python -m shzio.cli analyze-txt .\build\virtual-reality-buzzer-2.txt
python -m shzio.cli parts-info
python -m shzio.cli boards-info
python -m shzio.cli scan-saves "$HOME\Documents\My Games\SHENZHEN IO\76561198123244986"
python -m shzio.cli scan-custom "$HOME\Documents\My Games\SHENZHEN IO\76561198123244986\workshop"
python -m unittest discover -s tests
```

## Game metadata extraction

`extract-game` locates `Shenzhen.exe`, loads it with PowerShell reflection-only
APIs, and writes versioned JSON containing:

- the executable SHA-256 and assembly identity;
- all managed type names;
- detailed metadata for chip, pin, puzzle, terminal, solution, and trace types;
- decoded IL for the `ChipTypes` and `Puzzles` initialization paths;
- automatically discovered string-decoder candidates and embedded resources.

`decode-game-strings` statically decodes the resource using the exported IL; it
does not load or invoke the game assembly. `extract-chip-catalog` then splits
the `ChipTypes` initializer into all 66 records and extracts type IDs, prices,
sizes, unlocks, pin kinds, pin registers, physical pin slots, and exact contact
offsets for both supported orientations.

The 66 `ChipType` records are game component definitions, not 66 programmable
microcontrollers. They include processors, peripherals, displays, puzzle-only
devices, and editor objects. `extract-puzzle-catalog` separately recovers all
50 built-in puzzles, 198 terminal instances, 44 provided-chip instances, and
the raw board tile arrays. The arrays are read from PE initialized data with
Mono.Cecil. Consumer IL proves that each tuple is a texture index, clockwise
quarter-turn count, and flip flags (bit 0 horizontal, bit 1 vertical).

`extract-board-catalog` combines the puzzle and chip catalogs into normalized
board records. It preserves all tile data, resolves provided-part rotation and
absolute pin contacts, and distinguishes external terminal positions from
terminals bound to provided-chip pins. The current snapshot contains 50 boards,
7506 non-empty tiles, 198 terminals, 44 provided parts, and zero unresolved
terminal contacts. Editor IL establishes that texture indexes 1 and 9 are the
routable area, yielding 4979 routable cells, while ordinary parts must remain
inside the one-cell canvas margin. `load_board_spec("Sz035")` exposes the same
data as typed Python dataclasses.

The solution parser follows the game's trace encoding rather than treating it
as plain hexadecimal: `.` is an absent trace, `0` is an isolated trace with the
internal `Exists` bit set, and `1` through `F` are directional masks with that
same bit. Game coordinates have a bottom-left origin; `[traces]` rows are saved
from the highest y coordinate to the lowest.

`Solution` routes declared API nets after `build()` by default. The current
router handles two-pin and multi-endpoint logical nets, blocks unrelated pin
contacts, prevents networks from sharing trace cells, and retries deterministic
network orders. It preserves locked initial traces and can extend a component
that already touches the same logical network. It intentionally leaves bridge
crossings, route hints, negotiated congestion, and placement search to the next
routing stages.

The normalized catalogs are also executable runtime data. `part_from_type()`
can construct any of the 66 extracted component packages as a `Part`, while
`board_from_id()` and `Solution.board = "Sz035"` construct any of the 50 board
models with provided parts, external ports, terminal-to-pin bindings, routable
cells, placement bounds, and initial traces. Friendly manual names remain valid:
for example, a catalog RADIO binding recorded as pin index 3 resolves to
`radio.rx` even though its generated catalog alias is `x1`.

`compare-board-catalog` performs a three-state comparison between extracted
facts and a hand-written `Board`: comparable fields are `match` or `mismatch`,
while facts not represented by the hand-written `Board` remain `unresolved`.
For `Sz035`, all ten comparable structural and coordinate fields match; only
the hand-written class's absent tile layer remains unresolved.

MC4000, MC6000, and DX300 are checked against both local manuals while the
catalog is built. Manual-only facts such as program capacity, internal
registers, pin direction, and device-wide direction behavior are kept with
source-page provenance. Generated API aliases remain distinguishable from
official pin names; for example, DX300's three equivalent unnamed XBus
contacts use stable `x0`/`x1`/`x2` aliases with `official_name: null`.

The extractor does not require a .NET SDK, does not modify the executable, and
does not execute game static constructors. If the small Mono.Cecil dependency
is already available under `tools/.deps/`, it also reads PE initialized-data
fields without loading game code. Raw output under `data/raw/` is local and
ignored by Git.

The `scan-custom` and `custom-info` commands are intentionally metadata-only:
they extract `get_board()` ASCII layouts, terminal declarations, radio usage,
and dial declarations from custom puzzle Lua files without importing solution
walkthroughs.

## Optional hot reload patch

`tools/patch-hot-reload.ps1` patches the current supported `Shenzhen.exe` so
that opening a puzzle's solution browser clears the in-memory solution cache
and reloads solution files from disk. The game must be closed while applying
or restoring the patch, but it does not need to be restarted for later
solution edits: leave the puzzle and open it again.

The patch reloads the solution browser; it does not replace a circuit that is
already open in the editor. Return to mail before overwriting an existing
solution file so the game's delayed save cannot write the old in-memory circuit
back to disk. If the solution browser is already open, close it and reopen the
puzzle after changing files.

The patcher validates the original executable SHA-256, keeps an adjacent
`.shzio-hot-reload.original` backup, verifies the injected IL, and is
idempotent. It downloads the small Mono.Cecil 0.11.6 package from NuGet on its
first run; no C# SDK or Visual Studio installation is required.

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\patch-hot-reload.ps1 -Action status
powershell -ExecutionPolicy Bypass -File .\tools\patch-hot-reload.ps1 -Action apply
powershell -ExecutionPolicy Bypass -File .\tools\patch-hot-reload.ps1 -Action restore
```
