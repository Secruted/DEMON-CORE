from enum import Enum, auto  # [FIXED] 'from' must be lowercase

class Status(Enum):
    """
    Unified Status Language for:
    - Transport (Network / Proxy / Bot detection)
    - ContentParser (Intelligence / Analysis)
    - Harvester (Governor / Mission control)
    """

    # -------- TRANSPORT LAYER --------
    FAILED_FETCH = auto()        # Network error / timeout / blocked
    BLOCKED = auto()             # Explicit bot / captcha detection
    WAF_DETECTED = auto()        # [ADDED] Essential for Harvester logic (Missing in previous version)

    # -------- ROUTING / DISCOVERY --------
    ROUTER_SIGNAL = auto()       # Router produced candidate leads
    NO_NEW_ROUTES = auto()       # Router alive but exhausted

    # -------- CONTENT ANALYSIS --------
    SKIPPED_LOW_ENTROPY = auto()      # Content exists but below threshold
    SKIPPED_NO_TECH_SIGNALS = auto()  # Content exists but no patterns found
    EMPTY_CONTENT = auto()            # Page fetched but empty / broken

    # -------- SUCCESS PATH --------
    HARVEST_SUCCESS = auto()     # Valid secret extracted & verified

    # -------- SYSTEM STATES --------
    SYSTEM_IDLE = auto()         # No active targets
    SYSTEM_HEALTHY = auto()      # [FIXED] Moved to new line
