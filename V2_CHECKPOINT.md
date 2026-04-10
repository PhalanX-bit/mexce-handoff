# V2 Stabilization Checkpoint

Last updated: 2026-04-10
Branch: `codex/v2-stabilization`

## What Is Now Proven

The active V2 path is working as the main operational flow:

1. `Streamlit V2` creates or arms actions in `action_queue`
2. `core/api_executor.py` submits ARMED actions to the direct futures API
3. `core/reconcile_service.py` updates queue reconcile state
4. `Action Ledger` records queue, executor, reconcile, reprice, and lot events
5. `Lots` can be registered from fills or from manual recovery/backfill when exchange history is insufficient

This path is now the preferred architecture for the project.

## Confirmed Working Areas

- Active app entrypoint: `app/V2/streamlit_app.py`
- Queue lifecycle:
  - `PENDING -> ARMED -> RUNNING -> DONE/FAILED/CANCELED`
- Shared queue ordering:
  - lower `priority` first
  - then lower `id`
- Symbol normalization:
  - canonical futures form is used across queue, lots, dashboard, and cleanup scripts
- Leverage validation:
  - missing or invalid leverage is rejected earlier
- Contract meta fallback:
  - symbol metadata can fall back to `symbols_state`
- Bridge-free V2:
  - active V2 flow no longer depends on the old bridge path
- Legacy pending tracking:
  - `pending_limit_tasks` and `pending_chase_tasks` are no longer part of the active flow
- Lots:
  - `position_lots` is linked back to `action_queue` metadata
- Action Ledger:
  - `QUEUE_%`
  - `EXECUTOR_%`
  - `RECONCILE_%`
  - `REPRICE_%`
  - `LOT_%`
  are all treated as system events

## Confirmed Recovery / Fallback Behavior

MEXC order history and deals endpoints are not always sufficient for automatic fill confirmation.

When reconcile cannot confirm a fill because:

- `history_orders` returns `404`
- `deals` returns `404`
- or `deal_qty = 0`

the project now has a working manual recovery path:

1. identify a `DONE` action that was really filled
2. use `Lots -> Manual backfill from action_queue`
3. register the lot from the queue row
4. record the result in `Action Ledger` as `LOT_OPEN_REGISTERED` or related lot event

This fallback path has been proven in the current branch.

## Latest Proven Smoke Result

A live smoke cycle was confirmed for `action_id = 258`:

- queue events were recorded
- executor events were recorded
- reconcile events were recorded
- manual lot backfill created `lot_id = 10`
- `LOT_OPEN_REGISTERED` was written to `Action Ledger`
- `position_lots` now shows `source_action_id = 258`

This proves that V2 can preserve a usable operational history even when exchange-side fill history is incomplete.

## What Is Still Not Fully Automatic

- Automatic fill detection is still limited by MEXC history/deals availability
- Some fills may still require manual backfill from `action_queue`
- Close-flow automation is improved, but the strongest proven path today is:
  - open order handling
  - reconcile tracking
  - manual lot recovery when needed

## Legacy / Reference Only

These are no longer part of the preferred architecture:

- `app/streamlit_app.py`
- `app/bridge_api.py`
- `bridge/server.py`
- legacy pending task tables as a primary workflow

They may still exist for compatibility, history, or debugging.

## Recommended Next Milestone

The next stabilization milestone should focus on one of these:

1. Better automatic fill detection:
   - improve confirmation when exchange history is incomplete
2. Close-flow automation:
   - tighter handling of `CLOSE` actions and `lot_realizations`
3. Operations polish:
   - clearer ledger views
   - cleaner admin workflows
   - controlled cleanup of stale test/live rows

## Practical Rule Going Forward

When adding new work, assume:

- V2 is the active path
- `action_queue` is the main source of truth for actions
- `Action Ledger` is the operational history
- `Lots` is the position/fill view
- bridge and pending-task legacy layers should not gain new dependencies
