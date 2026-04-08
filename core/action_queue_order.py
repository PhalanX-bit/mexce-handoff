from __future__ import annotations

# Lower numeric priority should run first across the V2 action pipeline.
# The id tiebreak keeps ordering stable for rows with the same priority.
ACTION_QUEUE_EXECUTOR_ORDER_BY = "priority ASC, id ASC"
