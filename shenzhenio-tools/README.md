# shzio

External Python tooling for SHENZHEN I/O solution files.

See [ROADMAP.md](ROADMAP.md) for the complete execution plan, phase gates, and
current priorities.

Current scope:

- Parse and write solution `.txt` files.
- Model trace grids as directional bit masks.
- Model parts, pins, boards, and typed nets.
- Build solutions through a Python API.
- Run basic static checks before writing files back to the game save directory.
- Analyze physical trace nets from generated or existing save files.
- Extract board and terminal metadata from custom specification Lua files.

This is not a full simulator yet.

## Commands

Run from this directory with `PYTHONPATH=src` until the package is installed:

```powershell
$env:PYTHONPATH='src'
python -m shzio.cli check .\solutions\virtual_reality_buzzer.py
python -m shzio.cli build .\solutions\virtual_reality_buzzer.py -o .\build\virtual-reality-buzzer-2.txt
python -m shzio.cli analyze-txt .\build\virtual-reality-buzzer-2.txt
python -m shzio.cli parts-info
python -m shzio.cli boards-info
python -m shzio.cli scan-saves "$HOME\Documents\My Games\SHENZHEN IO\76561198123244986"
python -m shzio.cli scan-custom "$HOME\Documents\My Games\SHENZHEN IO\76561198123244986\workshop"
python -m unittest discover -s tests
```

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
