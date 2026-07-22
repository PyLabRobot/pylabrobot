# Writing PyLabRobot Device Drivers & Hello-World Guides

High level instructions for adding a new hardware device driver to PyLabRobot, plus the runnable tutorial that ships alongside it, for humans and agents.

Before you start, it's worth posting on the [PyLabRobot forum](https://discuss.pylabrobot.org) to say what you're working on — it helps avoid duplicated effort and is a good place to get support.

---

## 1. Understanding the device

Before writing a line of driver code, recover the protocol.

- **Extract, don't guess.** Work from whatever authoritative source you have — firmware/protocol documentation, log files produced by the manufacturer's software, or a reference binary. Pull out the real material: command frames, error codes, status values, and the exact human-readable text of every message. Don't leave placeholders — capture the *complete* set (e.g. every entry in an error-code switch), each filled with its real text.
- **Nail the wire format.** Establish transport (serial params, USB endpoint, socket), framing (start/end bytes, length fields, checksums), and the request/response handshake (echo? ack? busy→ok transitions?). Get it byte-exact.
- **Distinguish blocking from non-blocking operations.** Know which commands return immediately and which report `BUSY` until motion completes, and how a fault (e-stop, jam) surfaces.

---

## 2. Driver structure

Keep it small and idiomatic to PyLabRobot.

- **Default to one file, one plain class.** While some older drivers use a Driver/Backend split or other capability machinery, that is the old architecture and shouldn't be used anymore. Instead, write a single plain class whose public methods are the device's operations, talking to the hardware through a PyLabRobot `io` transport. Folder = vendor, file = device (`pylabrobot/<vendor>/<device>.py`); `__init__.py` re-exports. Promote the single `<device>.py` module to a `<device>/` package (a directory with `__init__.py`) split across several modules when it genuinely helps: a distinct subsystem, the wire protocol / framing layer, a large constant or command table. But only when warranted, not by reflex.
- **Use PyLabRobot's transport primitives** (`pylabrobot.io.serial.Serial`, etc.) rather than talking to the OS directly. Expose async `setup()` / `stop()` and public operation methods.
- **Stay OS-agnostic:** no OS-specific libraries or DLLs. The driver must work on Windows, Mac, and Linux — this is what keeps experiments portable and reproducible.
- **Prefer string literals over enums** for user-facing modes. A `Literal["standard", "head", "pump"]` argument plus an internal dict mapping those to wire codes reads better at the call site than an enum import.
- **Wire up the docs** the same way every other device does: add `docs/api/pylabrobot.<vendor>.rst` plus a line in `docs/api/pylabrobot.rst`.

### Idempotent public API

The public surface must expose **no non-idempotent commands.**

- If the hardware only offers a raw toggle/flip primitive, keep it private (`_toggle_x`) and expose move-to-state methods (`move_x_out` / `move_x_in`) that read current state, act only if needed, then confirm.
- This makes the API safe to call repeatedly and safe to reason about — the caller states intent ("be open"), not a blind toggle.

### Unverified drivers

If the driver has not been checked against real hardware, say so loudly. `setup()` should log a warning that the driver is untested and invite a change once someone verifies it on their device. Don't quietly present untested code as ready.

---

## 3. Code style

- **Comments document what the code does, not its history.** No "NEW", "now", "previously", "used to", "we changed". The code is not a diary or a changelog. State behavior as fact. Plain rationale ("why") is welcome; before/after narration and emphasis-caps are not.
- **No provenance stories.** Describe what the thing *is* and what it does. The archaeology of how it was produced belongs in your own notes, not the shipped repo.
- **No dead code.** Drop unused constants and leftover scaffolding.
- **No one-time scripts in the repo.** The codebase holds permanent software only. Run backfills, migrations, and throwaway jobs ad-hoc — as thin inline invocations that reuse the module's own functions — never as committed scripts. Keep reusable primitives in the main module so a one-off run is a one-liner.

---

## 4. The hello-world tutorial

Every device ships with a runnable "hello-world" notebook so a user can go from cabling to first successful command. They all follow one shape.

**Location & wiring:**

- File: `docs/user_guide/<vendor>/<device>/hello-world.ipynb`.
- Add `<vendor>/index.md` with a `{toctree}` listing `<device>/hello-world`.
- Add `<vendor>/index` to the Manufacturers `{toctree}` in `docs/user_guide/index.md` (keep it alphabetical).
- The API-reference `.rst` is separate from the user-guide notebook.

**Structure** (alternating markdown cell → code cell):

1. **Title + description + property table** (comms, serial settings, framing, value ranges) + a warning admonition if the driver is untested.
2. **"How it talks"** — a brief protocol overview. Do not get into details as the users care about using the machine and not about what happens on the wire.
3. **"Physical setup"** — cabling and connection parameters.
4. **"Connect"** — `setup()`.
5. **One section per operation** — status, load, prime, run, home, disconnect, and so on.

**Cell rules:**

- **One concept per code cell.** The deciding question: *would a user run this as one action or two?* If a physical action happens in between (place a plate, attach a tube), it's two cells. Loading a plate = `move_tray_out()` → *(place plate)* → `move_tray_in()` is two cells. Closely related read-only checks can share a cell.
- **Every code cell gets a preceding markdown cell** that explains it.
- **Use the idempotent public API** in every example — `move_tray_out()`, never a raw toggle.
- **Notebook mechanics:** edit with a notebook-aware tool (plain text replacement is blocked on `.ipynb`). Code cells use `execution_count: null` and empty `outputs`; `nbformat: 4`, `nbformat_minor: 5`; every cell has an `id`. Validate the JSON parses before shipping.

---

## 5. Verify & ship

- **Lint and type-check** with the repo's own tooling (ruff + mypy, 2-space indent).
- **Show the commit and PR text and get confirmation before committing.**
- **Never touch real hardware without an explicit, per-run go-ahead.** A previous approval does not carry to the next run.

---

## 6. Working style

- **Do exactly what's asked and nothing more.** No unrequested extras bundled in — no rewriting existing comments, no changing timeouts or constants, no redesigning logic, no docs/config side-quests. If a fix seems to need more, state the minimal fact and let the person decide.
- **Run at full speed.** Don't artificially throttle or slow-roll work; parallelize independent steps.
