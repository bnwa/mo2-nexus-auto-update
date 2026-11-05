You are working on a plugin for Mod Organizer 2 exclusively concerned with updating mods installed via Nexus Mods' intergration with Mod Organize 2.

## Development Environment
- Run Python, tests and coverage with the venv interpreter, as in `CONTRIB.md`
  (`.venv\Scripts\python -m unittest discover -s tests -t .`). Do not use the
  system or a WSL `python3`.

## Software Engineering Style
- Use guard clauses and early returns instead of nested else. A
plain else is fine for two symmetric branches.
- Parse unknown input once, at the boundary that owns it, into a
typed value. Pass that value inward; don't re-check it.
- Reify informal or implicit state machines as types with
functions. Signs of one: boolean flags that combine, fields that
are only valid in some phases, status strings or enums checked
ad hoc across call sites. Give each state its own type (sealed
interface, discriminated union, tagged variant) carrying only the
data valid in that state, and make each transition a function
from one state to the next. Illegal states should be
unrepresentable and branching exhaustive by the compiler. A
single independent flag stays a flag.
- Write the main function as the happy path: guards first, then
straight-line flow. Move bulky validation or supporting detail
into small helpers placed next to their caller.
- Don't extract a single-use helper unless it hides a genuinely
complex boundary, is complex enough to test directly (see Tests),
or has a name that makes the caller clearer. Otherwise inline it.
- Before adding complexity for a speculative case, ask me, with
one line each on the failure mode, likelihood, and cost. If you
can't ask, skip it and list it in your summary. Standard hardening
at trust boundaries (parameterized queries, path checks, no
secrets in logs) isn't speculative; do it.

### Tests
- Test behavior through the public API of the boundary that owns
it: the module, package, or service seam its callers depend on.
A class or function that exists only to serve one caller is an
implementation detail; cover it through that caller.
- Assert on observable outcomes: return values, resulting state,
effects at a real boundary (persisted rows, emitted events,
responses). Don't assert which collaborators were called, in what
order, or with what arguments.
- Never write change-detector tests: tests that restate the code
under test and would pass for a wrong implementation as readily
as a correct one. Mocking each delegate and verifying its call is
the common form. If a behavior-preserving refactor would break a
test, the test is wrong; rewrite it against behavior or delete it.
- Replace a dependency only where it crosses a process or
ownership boundary (network, clock, filesystem, randomness,
another team's service), and prefer a fake over a mock. Use real
instances for everything in-process.
- Don't test inputs the boundary already rules out. Inner code
receives parsed, typed values; its tests use those types and skip
cases the parser rejects.
- Test an implementation detail directly only when failures
through the public API wouldn't localize the defect: an
algorithm, a parser, a state machine's transition functions. Keep
its visibility as narrow as the language allows (package-private,
module-internal, unexported).
- If existing tests near your change are change detectors, don't
extend the pattern and don't rewrite them unasked; flag them in
your summary.

### Comments
- Comments don't narrate what code does. Comment only what code
can't say: a non-obvious why (constraint, workaround, invariant)
or the how of a genuinely intricate algorithm.
- Never put session content in code: requests, instructions,
review feedback, etc. Change history belongs in the commit
message.
- Never restate what the code or its names already say.
- Leave existing comments alone unless your change invalidates
one; then correct it.
- Doc comments on public API (Javadoc, docstrings, TSDoc, etc)
follow the repo's conventions and state the contract, not the
implementation.

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
