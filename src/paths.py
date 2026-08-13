from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
INPUT_DATA_DIR = DATA_DIR / "input"
LOCAL_MAP_PATH = INPUT_DATA_DIR / "map_local.json"
INSTANCES_DIR = DATA_DIR / "instances"

RESULTS_DIR = PROJECT_ROOT / "results"
ROLLING_RESULTS_DIR = RESULTS_DIR / "rolling"
DEFAULT_SCHEDULE_PATH = (
    ROLLING_RESULTS_DIR / "180_1200" / "instance_250_14400.json"
)

OUTPUT_DIR = PROJECT_ROOT / "output"
