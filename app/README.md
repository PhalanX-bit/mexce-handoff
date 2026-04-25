# App Entry Points

## Active UI

The active Streamlit UI for current work is:

- `V2/streamlit_app.py`

Use the repository root helper if you want a quick launcher:

- `../run_v2_app.bat`

## Legacy UI

The root-level `streamlit_app.py` in this folder is a legacy reference file.
Keep it for comparison and fallback context, but do not use it as the default
place for new feature work.

## Bridge API

The local bridge API file is:

- `bridge_api.py`

Current status:

- `bridge_api.py` is a deprecated compatibility layer for older browser/userscript flows.
- It is not the preferred execution path for V2.
- New work should prefer direct `action_queue` + `core/api_executor.py`.
- Legacy `pending_limit_tasks` creation from the bridge is disabled by default.

Other bridge-related files outside this folder, such as `../bridge/server.py`, are
historical/legacy only.

Dated copies in this folder are historical snapshots only.
