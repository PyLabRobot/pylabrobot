# PR Cleanup Checklist

## Before submitting PRs, strip `keyser-testing/` and keep only `pylabrobot/` changes.

---

## `air-liha-backend` → PR to `main`

### Files to INCLUDE in PR:
- [x] `pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py` (new)
- [x] `pylabrobot/liquid_handling/backends/tecan/__init__.py` (1 line added)
- [x] `pylabrobot/liquid_handling/liquid_classes/tecan.py` (8 entries appended)

### Files to EXCLUDE from PR:
- [ ] `keyser-testing/` — entire directory (test scripts, USB captures, manuals, DLLs)
- [ ] `claude.md` — local project instructions
- [ ] `.claude/` — Claude Code settings
- [ ] Any `taught_positions.json`, `labware_edits.json`

### Pre-PR checks:
- [ ] `ruff check pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py`
- [ ] `ruff format --check pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py`
- [ ] `mypy pylabrobot/liquid_handling/backends/tecan/air_evo_backend.py --check-untyped-defs`
- [ ] Existing EVO tests still pass: `pytest pylabrobot/liquid_handling/backends/tecan/EVO_tests.py`
- [ ] Remove debug print statements from `air_evo_backend.py`
- [ ] Remove temporary X offset (`x += 60`) or document as TODO
- [ ] Add docstring to `AirEVOBackend` explaining ZaapMotion requirements

### How to create clean PR branch:
```bash
git checkout air-liha-backend
git checkout -b air-liha-pr
git rm -r --cached keyser-testing/
git rm --cached claude.md
echo "keyser-testing/" >> .gitignore
git commit -m "Remove test artifacts for clean PR"
```

---

## `v1b1-tecan-evo` → PR to `v1b1`

### Files to INCLUDE in PR:
- [x] `pylabrobot/tecan/__init__.py` (new)
- [x] `pylabrobot/tecan/evo/__init__.py` (new)
- [x] `pylabrobot/tecan/evo/driver.py` (new)
- [x] `pylabrobot/tecan/evo/pip_backend.py` (new)
- [x] `pylabrobot/tecan/evo/air_pip_backend.py` (new)
- [x] `pylabrobot/tecan/evo/roma_backend.py` (new)
- [x] `pylabrobot/tecan/evo/evo.py` (new)
- [x] `pylabrobot/tecan/evo/errors.py` (new)
- [x] `pylabrobot/tecan/evo/firmware/__init__.py` (new)
- [x] `pylabrobot/tecan/evo/firmware/arm_base.py` (new)
- [x] `pylabrobot/tecan/evo/firmware/liha.py` (new)
- [x] `pylabrobot/tecan/evo/firmware/roma.py` (new)
- [x] `pylabrobot/tecan/evo/tests/__init__.py` (new)
- [x] `pylabrobot/tecan/evo/tests/driver_tests.py` (new)
- [x] `pylabrobot/legacy/liquid_handling/liquid_classes/tecan.py` (8 entries appended)

### Files to EXCLUDE from PR:
- [ ] `keyser-testing/` — entire directory
- [ ] `claude.md` — local project instructions
- [ ] `.claude/` — Claude Code settings

### Pre-PR checks:
- [ ] `ruff check pylabrobot/tecan/`
- [ ] `ruff format --check pylabrobot/tecan/`
- [ ] `mypy pylabrobot/tecan/ --check-untyped-defs`
- [ ] `pytest pylabrobot/tecan/evo/tests/ -v`
- [ ] Legacy EVO tests still pass: `pytest pylabrobot/legacy/liquid_handling/backends/tecan/EVO_tests.py`
- [ ] Remove any debug print statements
- [ ] Add unit tests for pip_backend, air_pip_backend, roma_backend
- [ ] Verify `TecanEVO` constructs correctly with all config combinations

### How to create clean PR branch:
```bash
git checkout v1b1-tecan-evo
git checkout -b v1b1-tecan-evo-pr
git rm -r --cached keyser-testing/
git rm --cached claude.md
echo "keyser-testing/" >> .gitignore
git commit -m "Remove test artifacts for clean PR"
```

---

## Common notes:
- `keyser-testing/` stays on our fork branches for ongoing development/testing
- The PR branches are ephemeral — created just for the PR, then deleted after merge
- The `labware_library.py` is local to our lab — NOT included in PRs
- USB captures, manuals, DLLs, and EVOware logs are investigation artifacts — NOT for upstream
