# Contributing

This guide gets you from a fresh checkout to a tested change. Read [`ARCH.md`](ARCH.md) for how the plugin is structured, and [`AGENTS.md`](AGENTS.md) for the engineering rules every change follows. Open work is in [`TODO.md`](TODO.md).

## Prerequisites

- **Windows.** The plugin and its tests target the same platform as Mod Organizer 2. Run the commands below in PowerShell from the repo root.
- **Python 3.12 (x64).** This is the version MO2 2.5.2 embeds. Install it with `winget install Python.Python.3.12` or from python.org.
  - The installer doesn't always put the `py` launcher on `PATH`. If `py` isn't found, call the interpreter by its full path. For a per-user install that is `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`.
  - Don't use the `python.exe` in `%LOCALAPPDATA%\Microsoft\WindowsApps`. It's the Microsoft Store shortcut, not an interpreter.
- **Git.**

MO2 itself is not needed to run the tests.

## Setup

Create a virtual environment in the repo root (it's gitignored) and install the test requirements:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.venv\Scripts\python --version
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r tests\requirements.txt
```

The second command should print `Python 3.12.x`. If Python is installed elsewhere, use that path in the first command, or `py -3.12` if the launcher is on `PATH`.

`tests\requirements.txt` installs:

- **PyQt6 6.7.1**, the version MO2 2.5.2 ships. The `ui/` and `plugin.py` tests use it.
- **coverage**, to measure test coverage.
- **pyright** (with its bundled Node.js) and **mobase-stubs 2.5.2**, for static type checking.

The plugin itself has no other dependencies.

To check that Qt can run without opening windows:

```powershell
.venv\Scripts\python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; from PyQt6.QtWidgets import QApplication; QApplication([]); print('ok')"
```

From Git Bash, use forward slashes instead: `.venv/Scripts/python.exe`.

## Running the tests

From the repo root:

```powershell
# Everything
.venv\Scripts\python -m unittest discover -s tests -t .

# The same, listing each test
.venv\Scripts\python -m unittest discover -s tests -t . -v

# One module, one class, or one test
.venv\Scripts\python -m unittest tests.test_domain_selection
.venv\Scripts\python -m unittest tests.test_app_session.LockTest
.venv\Scripts\python -m unittest tests.test_app_session.LockTest.test_locking_queued_dequeues_to_idle
```

The suite takes about a second. It doesn't touch MO2, the network or your MO2 instance. Qt tests run offscreen, so no windows appear.

### Coverage

Every production module is at 100% line and branch coverage. Keep it there:

```powershell
.venv\Scripts\python -m coverage run --branch --source=. --omit="tests/*,.venv/*" -m unittest discover -s tests -t .
.venv\Scripts\python -m coverage report -m
```

The `Missing` column lists uncovered lines and branches. Run `.venv\Scripts\python -m coverage html` for a browsable report in `htmlcov\`.

### Type checking

pyright is configured in `pyrightconfig.json`: `domain/`, `tracking/` and `app/` are checked in strict mode, everything else in standard mode. PyQt6 return values the stubs type as Optional are narrowed with `ui.qt_types.present`. The repo must stay clean:

```powershell
.venv\Scripts\pyright
```

### Without PyQt6

The non-Qt layers must not depend on Qt. Running the suite with a plain Python 3.12 (no PyQt6) checks this: Qt tests are skipped and everything else must pass.

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m unittest discover -s tests -t .
```

### Against the live Nexus API

`tests\test_nexus_live.py` runs the real v2 queries for a well-known mod. Use it to check whether the beta API still matches what the plugin expects. It needs network access and is skipped unless you opt in:

```powershell
$env:NEXUS_LIVE_TESTS = "1"
.venv\Scripts\python -m unittest tests.test_nexus_live -v
Remove-Item Env:NEXUS_LIVE_TESTS
```

## How the tests are organised

- **One test module per source module.** For example, `app/session.py` is tested in `tests/test_app_session.py`. `tests/test_architecture.py` enforces the dependency rules between layers in `ARCH.md`, so an import that crosses a layer boundary fails the suite.
- **`tests/__init__.py` sets up the environment before any test runs:**
  - It loads the repo as package `nexus_auto_updater` without running the root `__init__.py`.
  - It installs a stand-in `mobase` module. MO2's real one can't load outside MO2.
  - It sets `QT_QPA_PLATFORM=offscreen`.
  - It provides these helpers:
    - `QtTestCase`, a base class that skips without PyQt6 and creates the shared `QApplication`;
    - the `requires_qt` decorator;
    - `wait_until(predicate)`, which runs the Qt event loop until a condition holds, for code that uses threads.
- **`tests/fakes.py`** has fakes for the application's ports (`ModFileSource`, `Downloader`, `Installer`, `ModLocks`), a manual scheduler for `DownloadTracker` retries, and builders for `ModFile` and `InstalledMod`.
- **`tests/fake_mobase.py`** has the stand-in `mobase` module and a fake MO2 organizer, mod list, mods and download manager, used to test `host/mo2.py`.

When you write tests:

- Test the layer you changed through its own interface. Use real objects from lower layers and fakes for ports, as the existing tests do.
- Don't make real network requests or long sleeps. Patch `urlopen` for `nexus/graphql.py`, use the manual scheduler for tracker retries, and use `threading.Event` with `wait_until` for threads.
- Use Windows-style paths in fixtures, and real temporary files when parsing MO2's `meta.ini` or `.meta` files.
- Don't stub Qt. UI tests drive real widgets offscreen.

## Trying a change in MO2

The automated suite covers the plugin's own behaviour. What's left to check by hand is MO2's own behaviour, such as its install prompts and FOMOD wizards. `TODO.md` lists those checks under **Verification**.

1. Close MO2.
2. Make sure the plugin folder is at `<Mod Organizer 2>\plugins\nexus_auto_updater`. Either clone the repo there or copy it there.
3. Start MO2 and open **Tools → Nexus Auto Updater**. MO2 loads plugins only at startup, so restart it after each change.
4. If the plugin fails to load, check MO2's log in `<Mod Organizer 2>\logs`.

## Submitting a change

1. **Make the change in the right layer.** The tables "Dependency rules" and "Where to make common changes" in `ARCH.md` show where it belongs. Keep it small, and delete any code it makes obsolete.
2. **Add or update tests** in the matching `tests\test_<layer>_<module>.py`.
3. **Run the checks before you commit:**
   - the full suite;
   - the coverage report, which should still be at 100%;
   - pyright, which should report no errors;
   - the suite without PyQt6, if you touched anything outside `ui/` or `plugin.py`.
4. **Update the docs** if behaviour changed: `README.md` (for users), `ARCH.md` (structure) or `TODO.md` (open work).
5. **Commit** one coherent change at a time, using [Conventional Commits](https://www.conventionalcommits.org/). The scope names the layer, e.g. `fix(nexus): retry without dropped fields`. `AGENTS.md` has the full conventions.
6. **Keep the PR description short:** the problem, the change in behaviour, and the tests you ran.
