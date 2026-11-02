You are working on a plugin for Mod Organizer 2 exclusively concerned with updating mods installed via Nexus Mods' intergration with Mod Organize 2.

## Development Environment

- Run Python, tests and coverage with the venv interpreter, as in `CONTRIB.md`
  (`.venv\Scripts\python -m unittest discover -s tests -t .`). Do not use the
  system or a WSL `python3`.

## Engineering Style

- Change the least amount of files possible. A change that touches many files is
  more likely to be a bad change than a good one unless the broader scope is
  directly required.
- Prefer practical fixes over broad architecture work. Add abstractions only
  when they remove real repeated logic or establish a clear boundary of concern
  that surfaces amongst the current modules.
- Delete obsolete code aggressively when newer infrastructure makes it useless.
  Do not leave dead branches, unreachable code, or functions that are never
  called. If code is not necessary for the current behavior, remove it.

## Architecture Boundaries

- Keep each architectural layer focused on the concepts it owns. Do not leak 
  concerns into unrelated layers just because it is convenient to pass data
  through them.
- Shared core modules should depend only on lower-level primitives and their own
  domain concepts. Higher-level product concepts belong at the caller, component,
  or layer that already owns them.
- Pass the narrowest data needed across a boundary. Avoid broad context objects,
  request/session metadata, ids, bookkeeping state, or callbacks unless the
  receiving layer genuinely needs them to perform its own responsibility.
- Keep identity mapping, persistence bookkeeping,  response shaping, and UI state
  in the layers that own those jobs. Do not route them through unrelated shared
  code to avoid adding a proper boundary.
- Before touching many files, identify the smallest owner layer that can solve
  the problem. Spreadiing one feature across unrelated modules/layers/components
  needs a clear architectural reason, not just convenience.
- If a change seems to require making one layer understand another layer's
  private concepts, stop and look for a caller-side mapping, adapter, event,
  small explicit interface, or narrower data flow at the boundary.

## Commit and Review Habits

- If asked to write commit messages, follow the Conventional Commits standard:
  `<type>[optional scope][!]: <description>`, e.g. `feat: add per-mod version
  locks` or `fix(nexus): retry without dropped fields`. Use the types `feat`,
  `fix`, `docs`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `style` or
  `revert`. Write the description in the lowercase imperative with no trailing
  period. Scope, when used, names the layer (`domain`, `tracking`, `app`,
  `nexus`, `host`, `ui`). Mark breaking changes with `!` and a
  `BREAKING CHANGE:` footer.
- Keep PR descriptions short and reviewable. State the problem, the behavioral
  change, and the tests run; avoid long narrative explanations, implementation
  diaries, or exhaustive file-by-file summaries unless the reviewer explicitly
  needs that context.
- Prefer one coherent behavioral change per commit. Dependency pins, tests, and
  the code that needs them may be in the same commit when they are inseparable.
- In reviews, prioritize real user impact: crashes, wrong dtype/device behavior,
  memory regressions, broken model loading, workflow incompatibility, and noisy
  or misleading user-facing output.
