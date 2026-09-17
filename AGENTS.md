# Repository Guidelines

## Project Structure & Module Organization

VisionGuide is a Python computer-vision system for Raspberry Pi with a PC simulator.
Source is split by **where the code runs**:

- `device/` — the 18 modules that run on the Pi. The edge entry point is
  `device/camera_live_pi.py`; `device/yolo_postprocess.py` holds the shared
  pre/post-processing, and the rest cover tracking, audio, GPIO, and RF triggers.
  **This directory must stay in sync with `DEPLOY_PY` in the `Makefile`.**
- `tools/` — PC-only scripts, never deployed: `tools/data/` (dataset preparation),
  `tools/eval/` (benchmarks), `tools/dev/` (`camera_live.py` PC viewer, helpers).
- `apps/` — things a person launches: `apps/roi_editor/` (FastAPI + static HTML,
  the UI that actually runs on the Pi), `apps/simulator/` (Streamlit),
  `apps/label_tool/`.
- `dashboard/` — the unimplemented React admin dashboard plus design references.
  **It has no deployment path to the Pi.**
- `tests/`, `deploy/` (systemd units), `configs/` (training YAML and
  `configs/examples/` sample JSON), `docs/`,
  `datasets/`, `runs/` (model outputs), and `weights/`.

Keep local `rois.json`, `camera_config.json`, recordings, databases, and
environment files out of commits.

### The Pi layout is flat — it does not mirror this repository

`make sync` unpacks `device/*.py` into `~/visionguide/` on the Pi **as a flat
directory**; there is no `device/` on the device, and the systemd units point at
`~/visionguide/camera_live_pi.py`. Any path computation that has to work in both
places must detect the layout rather than assume one:

```python
_HERE = Path(__file__).parent
_BASE = _HERE if (_HERE / "runs").is_dir() else _HERE.parent   # flat (Pi) vs nested (PC)
```

The same applies to the `simulator` package (`~/visionguide/simulator/` on the Pi,
`apps/simulator/` here). Be careful: `camera_live_pi.py` imports `ROIManager`
inside a `try/except ImportError`, so a wrong path does not raise — it silently
disables ROI and audio.

When adding a module, first ask whether it runs on the Pi. If it does, put it in
`device/` **and add it to `DEPLOY_PY`**; otherwise put it under the matching
`tools/` subdirectory.

## Build, Test, and Development Commands

From the repository root:

- `conda env create -f environment.yml` creates the recommended Python 3.10
  environment; alternatively `pip install -r requirements.txt`.
- `python -m pytest tests/ -v` runs the full suite. `tests/conftest.py` puts
  `device/`, `apps/`, and `tools/*` on `sys.path` — do not re-add `sys.path`
  manipulation inside individual test files.
- `cd apps/simulator && streamlit run app.py` starts the PC simulator;
  `apps/simulator/requirements.txt` lists its extra dependencies.
- `make deploy PI=<raspberry-pi-ip>` syncs code/models and installs Pi
  dependencies. `make sync` updates code and models only, `make sync-roi-editor`
  updates the web UI, `make run-headless` starts the MJPEG service, and
  `make ping` verifies SSH/Python access.
  **Partial deploys need both `sync` and `sync-roi-editor`** — running only the
  first leaves a stale web UI on the device.

## Coding Style & Naming Conventions

Python 3.10+, four-space indentation, small readable functions, and explicit
validation at hardware/configuration boundaries. Follow the existing `snake_case`
modules and functions, `PascalCase` classes, and `UPPER_CASE` constants. Tests use
`test_*.py` files and `test_*` functions. No formatter or linter is configured;
preserve the surrounding style and keep changes narrowly scoped.

## Testing Guidelines

Add or update pytest coverage for behavior changes, especially ROI geometry,
camera backends, configuration validation, event persistence, and hardware
fallbacks. Prefer temporary paths and monkeypatching so tests need no camera,
GPIO, Coral TPU, audio device, or network. Run the full suite before submitting.

## Commit & Pull Request Guidelines

Use the conventional prefixes seen in history — `feat:`, `fix:`, `docs:`,
`refactor:`, `chore:` — followed by an imperative summary. Pull requests should
explain the behavior change, list the tests run, identify Pi/model/configuration
impact, and include screenshots or recordings for UI changes. Link the relevant
issue when there is one. Read `CLAUDE.md` for repository-specific implementation
context, and `docs/FILE_INVENTORY.md` for where things live, before making
substantial changes.
