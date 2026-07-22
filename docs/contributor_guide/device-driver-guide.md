# Writing PyLabRobot Device Drivers & Hello-World Guides

How to add a device driver and its hello-world notebook, for humans and agents. Post on the [PyLabRobot forum](https://discuss.pylabrobot.org) before starting to avoid duplicated effort and get support.

## 1. Understand the device

Recover the protocol before writing code.

- **Extract, don't guess.** Work from an authoritative source — firmware/protocol docs, manufacturer log files, or a reference binary. Capture the *complete* set of command frames, error codes, status values, and exact message text, filled with real values, not placeholders.
- **Get the wire format byte-exact:** transport (serial params / USB endpoint / socket), framing (delimiters, length fields, checksums), handshake (echo? ack? busy→ok?).
- **Note blocking vs non-blocking commands** and how faults (e-stop, jam) surface.

## 2. Structure the driver

Keep it small and idiomatic to PyLabRobot.

- **One file, one plain class.** The old Driver/Backend split and capability machinery are deprecated — don't use them. Instead write a single plain class whose public methods are the device's operations, talking to hardware through a `pylabrobot.io` transport. Path `pylabrobot/<vendor>/<device>.py`, re-exported from `__init__.py`. Promote to a `<device>/` package only when it genuinely helps — a distinct subsystem, the protocol/framing layer, or a large command table — not by reflex.
- **Stay OS-agnostic:** no OS-specific libraries or DLLs. Running on Windows, Mac, and Linux is what keeps experiments portable and reproducible.
- **Async `setup()` / `stop()`** plus public operation methods, over PyLabRobot's transport primitives (`pylabrobot.io.serial.Serial`, etc.) — never the OS directly.
- **Prefer string `Literal[...]` over enums**, especially anything user-facing. A `Literal["standard", "head", "pump"]` argument plus an internal dict mapping to wire codes reads better at the call site than an enum import. `IntEnum` is fine in narrow internal cases (e.g. a fixed set of wire/register codes never exposed to callers).
- **API docs:** add `docs/api/pylabrobot.<vendor>.rst` plus a line in `docs/api/pylabrobot.rst`.

### Idempotent public API

The public surface must expose **no non-idempotent commands.** If the hardware only offers a raw toggle/flip, keep it private (`_toggle_x`) and expose move-to-state methods (`move_x_out` / `move_x_in`) that read current state, act only if needed, then confirm. This keeps the API safe to call repeatedly — the caller states intent ("be open"), not a blind toggle.

### Unverified drivers

If the driver hasn't been checked against real hardware, say so loudly: `setup()` should `logger.warning(...)` that it's untested and invite a change once someone verifies it. Don't quietly present untested code as ready.

## 3. Code style

- **Comments document what the code does, not its history.** No "NEW", "now", "previously", no emphasis-caps — the code is not a diary. State behavior as fact; plain rationale ("why") is welcome.
- **No provenance stories.** Describe what the thing *is*, not where it came from — in code, commits, or PRs.
- **No dead code**, and **no one-time scripts in the repo.** The codebase holds permanent software only; run backfills/migrations ad-hoc as thin inline invocations of the module's own functions.

## 4. Hello-world notebook

Every device ships a runnable notebook that takes a user from cabling to first command. Path `docs/user_guide/<vendor>/<device>/hello-world.ipynb`; wire it in via the `<vendor>/index.md` `{toctree}` and add `<vendor>/index` to the Manufacturers `{toctree}` in `docs/user_guide/index.md` (alphabetical).

Sections (markdown cell then code cell): (1) title + property table + untested-warning; (2) how it talks — brief, since users care about the machine, not the wire; (3) physical setup; (4) `setup()`; (5) one section per operation.

Cell rules:
- **One concept per code cell.** If a physical action happens between steps, that's two cells: `move_tray_out()` → place plate → `move_tray_in()`. Every code cell gets a preceding markdown cell.
- **Notebook JSON:** edit with a notebook-aware tool (plain-text replace is blocked on `.ipynb`); code cells `execution_count: null`, empty `outputs`; `nbformat: 4`, `nbformat_minor: 5`; every cell has an `id`; validate it parses.

## 5. Verify & ship

- **Lint + type-check:** ruff + mypy, 2-space indent.
- **Show commit + PR text and get confirmation before committing.**
- **Never drive real hardware without explicit per-run approval** — a previous OK doesn't carry to the next run.

## 6. Working style

- **Do exactly what's asked, nothing more** — no drive-by edits to comments/constants/logic, no side-quests. If a fix seems to need more, state the minimal fact and let the person decide.
- **Run at full speed;** parallelize independent work.
