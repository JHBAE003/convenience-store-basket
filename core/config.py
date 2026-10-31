from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
TEST_DATA_PATH = BASE_DIR / "sample_data" / "demo_products.csv"

# TEST: CSV only (recommended for class/demo)
# LIVE: use live or cached results; failed brands without a cache show errors
DEFAULT_MODE = "TEST"

LIVE_MCP_ENDPOINT = os.getenv("CONVENIENCE_MCP_ENDPOINT", "https://mcp.aka.page/")
REQUEST_TIMEOUT_SECONDS = 12
def _seconds(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


# Application caches only. The relay may serve its own older cached response.
INVENTORY_CACHE_TTL_SECONDS = _seconds("CONVENIENCE_INVENTORY_CACHE_SECONDS", 60)
PRODUCT_CACHE_TTL_SECONDS = _seconds("CONVENIENCE_PRODUCT_CACHE_SECONDS", 900)
STORE_CACHE_TTL_SECONDS = _seconds("CONVENIENCE_STORE_CACHE_SECONDS", 1800)
PROMOTION_CACHE_TTL_SECONDS = _seconds("CONVENIENCE_PROMOTION_TTL_SECONDS", 21600)
FAILURE_COOLDOWN_SECONDS = _seconds("CONVENIENCE_FAILURE_COOLDOWN_SECONDS", 3)
LIVE_CACHE_TTL_SECONDS = INVENTORY_CACHE_TTL_SECONDS

BRANDS = ["GS25", "CU", "세븐일레븐", "이마트24"]

# Default map/search starting point: Seoul National University.
DEFAULT_LAT = 37.458115
DEFAULT_LNG = 126.952161
DEFAULT_RADIUS_KM = 2.0
