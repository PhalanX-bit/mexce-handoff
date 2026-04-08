# MEXC V2 Workspace

This repository is currently being stabilized on branch `codex/v2-stabilization`.

## Active app entrypoint

The active Streamlit app for ongoing work is:

- `app/V2/streamlit_app.py`

This is the V2 UI that is split into dedicated tabs and backed by the
`core/streamlit_services` layer.

## Active code areas

The main places for new work are:

- `app/V2/`
- `core/streamlit_services/`
- `core/api_executor.py`
- `core/reconcile_service.py`
- `core/reprice_service.py`
- `core/reprice_worker.py`
- `scripts/run_api_executor_once.py`
- `scripts/run_api_executor_loop.py`

## Legacy/reference areas

These files are kept for history, comparison, and fallback reference. They are
not the preferred place for new feature work:

- `app/streamlit_app.py`
- dated copies under `app/`
- dated copies under `core/`

## Current V2 pipeline

The active flow is:

1. Strategy or manual UI creates actions in `action_queue`
2. Actions move through `PENDING -> ARMED -> RUNNING -> DONE/FAILED/CANCELED`
3. `core/api_executor.py` executes ARMED actions through the direct futures API
4. Reconcile/reprice services operate on the same queue state

## Local data and secrets

These stay local and are intentionally ignored by git:

- `.env`
- `data/*.sqlite`
- `logs/`

## Typical local run

Run the active V2 UI:

```powershell
streamlit run app/V2/streamlit_app.py
```

Or use:

```powershell
run_v2_app.bat
```

## First stabilization goal

The first milestone on this branch is to make V2 the clear active path and
reduce confusion between the V2 modules and legacy files.
