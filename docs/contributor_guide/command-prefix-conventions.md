# Command Prefix Conventions

<hr>

PyLabRobot uses a small set of standardized verb prefixes on method names. The prefix tells the caller two things at a glance:

1. **Which world the method operates in** — the physical machine, or PyLabRobot's in-memory resource model.
2. **What kind of effect it has** — does it act, measure, read state, or write state?

Following these conventions keeps the API predictable, makes code reviews faster, and removes whole categories of *"what does this method actually do?"* confusion.

<details style="background-color:#f8f9fa; border-left:5px solid #007bff; padding:10px; border-radius:5px;">
    <summary style="font-weight: bold; cursor: pointer;">Note: For AI coding assistants reading this repo</summary>
    <hr>
    <p>This convention is partially adopted. A significant fraction of existing methods predate it and use names from before this convention existed. Treat this document as the source of truth when:</p>
    <ul>
        <li>reviewing a PR that adds or renames a method,</li>
        <li>being asked to "audit," "harmonize," or "standardize" names in a module,</li>
        <li>choosing a name for any new public method.</li>
    </ul>
    <p><strong>Do not silently rename existing methods to align with the convention as part of unrelated work.</strong> Harmonization is a separate, dedicated effort; flag non-compliant names you encounter, but keep your PR scoped to the task you were asked to do.</p>
</details>

<hr>

## The standard

Two domains (physical machine vs in-memory resource model), six categories, one prefix table per domain. The rest of this document explains the reasoning and edge cases; **this section is the convention**.

### Physical machine commands

| Category    | Prefixes                                              | Meaning                                                                              |
|-------------|-------------------------------------------------------|--------------------------------------------------------------------------------------|
| ACTION      | `move_`, `aspirate_`, `dispense_`, `pickup_`, `shake_` | Command the machine to do something physical.                                       |
| MEASUREMENT | `measure_`, `read_`, `sense_`, `capture_`             | Trigger a transducer/sensor and return the sampled value.                            |
| MEM-READ    | `request_`                                            | Ask the machine to return a value it already holds (register, EEPROM, status flag).  |
| MEM-WRITE   | `set_`                                                | Write a configuration value to the machine (register, EEPROM, parameter).            |

### Resource-model commands

| Category | Prefixes                          | Meaning                                                                  |
|----------|-----------------------------------|--------------------------------------------------------------------------|
| QUERY    | `get_`                            | Look up a resource or value from the in-memory model.                    |
| UPDATE   | `update_`, `assign_`, `unassign_` | Mutate the in-memory model (change a tracked value, attach/detach resources). |

### Forbidden synonyms

Do not introduce new methods using these prefixes. They duplicate categories already covered above and dilute the convention:

`fetch_`, `retrieve_`, `obtain_`, `acquire_`, `grab_`, `pull_`, `poll_`, `query_` (use `get_` for the model or `request_` for the machine), `write_` (use `set_`), `put_` (use `set_` or `assign_`), `add_*_to_*` / `remove_*_from_*` for parent–child operations (use `assign_` / `unassign_`).

If you believe an existing prefix is wrong for your case, please open a discussion on the forum before adding a new verb.

<hr>

## Choosing the right prefix

Walk down this decision flow when naming a new method.

1. **Does the method touch hardware at all?**
    - **No** → resource-model command. Go to step 4.
    - **Yes** → physical-machine command. Go to step 2.
2. **Does the method cause physical motion, fluid transfer, or otherwise change the state of the world?** Use an ACTION prefix.
3. **Does it return a value?**
    - **Value comes from a transducer/sensor sample** (absorbance, weight, capacitive tip sense, camera image, temperature reading) → MEASUREMENT prefix. Use the verb that fits the modality: `measure_`, `read_`, `sense_`, `capture_`.
    - **Value comes from the machine's stored state** (a serial number, a configured offset, a status flag, an EEPROM register) → `request_`.
    - **Method writes a value into the machine's stored state** → `set_`.
4. **Resource-model only.**
    - **Returns something** (single object or a filtered collection) → `get_`.
    - **Mutates a tracked value** (e.g. liquid volume in a well) → `update_`.
    - **Attaches/detaches a child resource** → `assign_` / `unassign_`.

### Picking among the MEASUREMENT verbs

All four verbs mean *"trigger a sample and return the value."* **`measure_` is the default** — use it unless one of the other three reads more naturally for the specific modality:

- `measure_` — the default. Pairs naturally with most physical quantities: `Scale.measure_weight`, `ScilaBackend.measure_temperature`.
- `read_` — accepted for quantities idiomatically "read off" an instrument display, mainly photometric plate-reader signals: `Reader.read_absorbance`, `Reader.read_fluorescence`, `Reader.read_luminescence`.
- `sense_` — for discrete/boolean detection where the natural verb is "sense": capacitive tip sense, optical break-beam, limit-switch state. *Reserved; no methods currently use this prefix.*
- `capture_` — for imaging, where "capture" is the standard verb: `capture_image`, `capture_well`. The current public method `Imager.capture()` is a bare verb under this convention and a candidate for harmonization.

When in doubt, pick `measure_`. If a related method on the same class already uses `read_` for an analogous quantity, match it rather than introducing a sibling prefix.

<hr>

## Background

<details style="background-color:#f8f9fa; border-left:5px solid #007bff; padding:10px; border-radius:5px;">
    <summary style="font-weight: bold; cursor: pointer;">Why this exists</summary>
    <hr>
    <p>Without a convention, equivalent operations end up with inconsistent names. The same method might be called <code>get_temperature</code> on one backend, <code>read_temperature</code> on another, and <code>request_temperature</code> on a third — even though all three round-trip to the device. Worse, <code>get_resource</code> (a pure in-memory lookup) and <code>get_temperature</code> (a firmware query that blocks on serial I/O) look identical at the call site despite having very different cost and failure modes.</p>
    <p>The discussion that led to this convention lives on the forum: <a href="https://discuss.pylabrobot.org/t/standardised-plr-command-prefix-proposal/403">Standardised PLR Command Prefix Proposal</a>.</p>
</details>

<details style="background-color:#f8f9fa; border-left:5px solid #007bff; padding:10px; border-radius:5px;">
    <summary style="font-weight: bold; cursor: pointer;">The two axes behind the standard</summary>
    <hr>
    <p>The two domains in the standard above — physical machine and resource model — are governed by different prefixes precisely because they have different cost and failure modes. A machine <code>request_</code> call round-trips over serial and can time out; a model <code>get_</code> call is an in-memory lookup that cannot fail in the same way. Conflating them means callers cannot tell from the name what they are about to pay for.</p>
    <p><strong>Axis 1 — Domain:</strong></p>
    <ul>
        <li><strong>Physical machine</strong>: anything that talks to hardware (sends a firmware command, reads a sensor, queries an EEPROM register, sets a configuration value on the device). These methods are almost always <code>async</code> and can fail with hardware errors.</li>
        <li><strong>Resource model (RMS)</strong>: PyLabRobot's in-memory representation of the physical setup. The deck layout, plates, tubes, tips, liquid volumes, and parent/child relationships all live here, as do the <strong>trackers</strong> that mirror state the hardware itself doesn't report back — most notably <a href="https://github.com/PyLabRobot/pylabrobot/blob/main/pylabrobot/resources/tip_tracker.py"><code>TipTracker</code></a> (which channel holds which tip) and <a href="https://github.com/PyLabRobot/pylabrobot/blob/main/pylabrobot/resources/volume_tracker.py"><code>VolumeTracker</code></a> (how much liquid is in each container). RMS methods are synchronous and side-effect-free with respect to hardware. Their job is to keep PLR's view of the lab in sync with what's actually on the deck, so the rest of the codebase can reason about state without having to interrogate the machine.</li>
    </ul>
    <p><strong>Axis 2 — Effect:</strong></p>
    <ul>
        <li><strong>Acts on the world</strong> (moves something, transfers liquid, triggers a measurement).</li>
        <li><strong>Reads</strong> a value (from hardware or from the model).</li>
        <li><strong>Writes</strong> a value (configures hardware or mutates the model).</li>
    </ul>
</details>

<hr>

## Edge cases

**"Position" — measurement or memory read?** If the position is sampled via an encoder query that the firmware returns from a cached register, prefer `request_position` (state read). If a fresh encoder sample is triggered, prefer `measure_position`. When the distinction is invisible to the caller and the firmware itself blurs it, default to `request_`.

**Methods that act *and* return a value.** Some action methods naturally return data (e.g. an aspirate that returns the actual displaced volume from a pressure trace). Name by the primary effect — the action — and document the return value. `aspirate_` stays `aspirate_`, even though it returns something.

**Methods that update both model and hardware.** A backend method that sets a hardware parameter *and* records the new value in the resource model is still primarily a machine write — use `set_`. The model update is an implementation detail of staying in sync.

**Properties vs methods.** For trivial, cheap, side-effect-free reads of model state, prefer a `@property` over a `get_` method (`plate.num_items`, not `plate.get_num_items()`). The prefix convention applies to methods; properties are exempt.

<hr>

## Quick examples

All method names below are verified against the current codebase. Names marked `# COMPLIANT` follow the convention as written; names marked `# NON-COMPLIANT` exist today but violate the convention and are candidates for harmonization.

```python
# Physical machine — COMPLIANT
await backend.move_channel_x(channel=0, x=100)         # ACTION
await backend.aspirate(...)                            # ACTION
absorbance = await reader.read_absorbance(plate, ...)  # MEASUREMENT
weight = await scale.measure_weight()                  # MEASUREMENT
present = await backend.request_tip_presence()         # MEM-READ
serial = await el406.request_serial_number()           # MEM-READ
await star.set_x_offset_x_axis_iswap(x_offset=0)       # MEM-WRITE

# Resource model — COMPLIANT
plate = deck.get_resource("plate_01")                  # QUERY
items = plate.get_items(["A1", "B1"])                  # QUERY
deck.assign_child_resource(plate, location=...)        # UPDATE
deck.unassign_child_resource(plate)                    # UPDATE

# Existing methods that do NOT yet follow this convention
# (these are real methods today and are valid harmonization targets):
await imager.capture(...)         # NON-COMPLIANT  — bare verb; should be e.g. `capture_image`
well.set_volume(50.0)             # NON-COMPLIANT  — model mutation using machine-reserved `set_`;
                                  #                  should be `update_volume`
tracker.add_liquid(50.0)          # NON-COMPLIANT  — should be e.g. `update_liquid` (UPDATE);
                                  #                  same for `remove_liquid`, `add_tip`, `remove_tip`
await vantage.query_tip_presence()  # NON-COMPLIANT — uses forbidden `query_` synonym
                                    #                  (note Vantage also exposes the compliant
                                    #                  `request_tip_presence` alongside it)
```

<hr>

## Discussion

Naming questions and proposed additions to this convention belong on the [forum](https://discuss.pylabrobot.org). When proposing a new prefix, include the category it would join, why none of the existing prefixes fit, and one or two real call-site examples.
