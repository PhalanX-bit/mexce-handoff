from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.api_executor import process_one_action, print_action_status

ACTION_ID = 245

print("ROOT_DIR =", ROOT_DIR)

print("\n=== BEFORE ===")
print_action_status(ACTION_ID)

print("\n=== PROCESS ===")
process_one_action(ACTION_ID, verbose=True)

print("\n=== AFTER ===")
print_action_status(ACTION_ID)