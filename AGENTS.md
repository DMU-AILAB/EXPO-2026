# Repository Guidelines

## Project Structure & Module Organization

VisionGuide is a Python computer-vision system for Raspberry Pi and PC simulation. Root modules contain camera pipelines, YOLO inference, tracking, GPIO/audio triggers, configuration, and event/statistics helpers. The main edge entry point is `camera_live_pi.py`; `camera_live.py` provides the PC camera path. `simulator/` contains the Streamlit simulator and ROI logic, while `roi_editor/` and `label_tool/` provide FastAPI-based tools with static HTML UIs. Tests are in `tests/`, deployment services in `deploy/`, research/deployment notes in `docs/`, training data in `datasets/`, and generated model outputs in `runs/`. Keep local `rois.json`, `camera_config.json`, recordings, databases, and environment files out of commits.

## Build, Test, and Development Commands

From the repository root:

- `conda env create -f environment.yml` creates the recommended Python 3.10 environment; alternatively use `pip install -r requirements.txt`.
- `python -m pytest tests/ -v` runs the full test suite. Use a specific file, such as `python -m pytest tests/test_roi_manager.py -q`, for focused work.
- `streamlit run simulator/app.py` starts the PC simulator; `simulator/requirements.txt` lists its additional dependencies.
- `make deploy PI=<raspberry-pi-ip>` synchronizes code/models and installs Pi dependencies. Use `make sync` for code/model-only updates, `make run-headless` for the MJPEG service, and `make ping` to verify SSH/Python access.

## Coding Style & Naming Conventions

Use Python 3.10+ with four-space indentation, readable small functions, and explicit validation at hardware/configuration boundaries. Follow existing `snake_case` module/function names, `PascalCase` classes, and descriptive constants in `UPPER_CASE`. Tests use `test_*.py` files and `test_*` functions. No repository formatter or linter is configured; preserve surrounding style and keep changes narrowly scoped.

## Testing Guidelines

Add or update pytest coverage for behavior changes, especially ROI geometry, camera backends, configuration validation, event persistence, and hardware fallbacks. Prefer temporary paths and mocks/monkeypatching so tests do not require a camera, GPIO, Coral TPU, audio device, or network. Run the full suite before submitting changes.

## Commit & Pull Request Guidelines

Use concise conventional prefixes seen in history, for example `feat:`, `fix:`, `docs:`, or `style:`, followed by an imperative summary. Pull requests should explain the behavior change, list tests run, identify Pi/model/configuration impact, and include screenshots or recordings for UI changes. Link the relevant issue or task when available. Read `CLAUDE.md` for repository-specific implementation context before making substantial changes.
