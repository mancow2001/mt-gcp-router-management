import os
import ipaddress
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from a .env file into the runtime environment
load_dotenv()

@dataclass
class Config:
    """
    Central configuration class that loads and stores all environment-defined
    parameters for the healthcheck daemon.

    All fields are populated from environment variables and type-cast as needed.

    Attributes:
        Logging:
            - logger_name: Daemon Name Logger will log as. 
            - log_level: Log verbosity (e.g., DEBUG, INFO, WARNING).
            - log_file: Path to optional log file.
            - log_max_bytes: Log file size before rotation.
            - log_backup_count: Number of rotated backups to retain.
            - enable_gcp_logging: Enable/disable Stackdriver logging.
            - enable_structured_console: Output JSON to console for structured events.
            - enable_structured_file: Output JSON to separate structured log file.
            - structured_log_file: Path to structured JSON log file.

        GCP & Regions:
            - gcp_project: GCP project ID.
            - gcp_credentials: Path to the service account JSON file.
            - local_region / remote_region: GCP regions to monitor.
            - local_bgp_router / remote_bgp_router: BGP router identifiers.
            - local_bgp_region / remote_bgp_region: Regions used in BGP tagging.
            - bgp_peer_project: GCP project ID for peered resources.

        Network Prefixes:
            - primary_prefix / secondary_prefix: IPv4 prefixes being advertised.
            - primary_internal_ip / secondary_internal_ip: Internal IPs tied to those prefixes.

        Cloudflare:
            - cf_account_id: Cloudflare account ID.
            - cf_api_token: API token for Cloudflare's Magic Transit.
            - cf_desc_substring: Substring to match route descriptions.
            - cf_primary_priority / cf_secondary_priority: Priority values to toggle.

        Runtime Control:
            - check_interval: Seconds between health checks.
            - max_retries: Legacy retry setting (deprecated, use per-service retries).
            - max_retries_health_check: Retries for backend health checks (read-only, can be higher).
            - max_retries_bgp_check: Retries for BGP session status checks.
            - max_retries_bgp_update: Retries for BGP advertisement updates (modifies state, should be lower).
            - max_retries_cloudflare: Retries for Cloudflare API calls.
            - initial_backoff / max_backoff: Exponential backoff timing.
            - cb_threshold / cb_timeout: Circuit breaker limits.
            - run_passive: When TRUE, daemon runs but skips all route updates.

        State Verification & Stability:
            - state_2_verification_threshold: Consecutive State 2 detections required before acting (local unhealthy).
            - state_3_verification_threshold: Consecutive State 3 detections required before acting (remote unhealthy).
            - state_4_verification_threshold: Consecutive State 4 detections required before acting (both unhealthy).
            - health_check_window: Number of recent health check results to track for hysteresis.
            - health_check_threshold: Minimum healthy checks in window to consider region healthy.
            - asymmetric_hysteresis: Use different thresholds for healthy→unhealthy vs unhealthy→healthy.
            - min_state_dwell_time: Minimum seconds in a state before allowing transitions (prevents flapping).
            - dwell_time_exception_states: State codes exempt from dwell time requirement (e.g., [1, 4]).

        API Timeouts:
            - gcp_api_timeout: General GCP API call timeout.
            - gcp_backend_health_timeout: Backend health check timeout.
            - gcp_bgp_operation_timeout: BGP advertisement update timeout.
            - cloudflare_api_timeout: Cloudflare API request timeout.
            - cloudflare_bulk_timeout: Cloudflare bulk update timeout.
    """
    # Logging
    logger_name: str = os.getenv('LOGGER_NAME', 'HEALTH_CHECK_DAEMON').upper()
    log_level: str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_file: str | None = os.getenv('LOG_FILE', '/var/log/radius_healthcheck_daemon.log')
    log_max_bytes: int = int(os.getenv('LOG_MAX_BYTES', 10 * 1024 * 1024))
    log_backup_count: int = int(os.getenv('LOG_BACKUP_COUNT', 5))
    enable_gcp_logging: bool = os.getenv('ENABLE_GCP_LOGGING', 'false').lower() == 'true'
    
    # NEW: Structured logging options
    enable_structured_console: bool = os.getenv('ENABLE_STRUCTURED_CONSOLE', 'false').lower() == 'true'
    enable_structured_file: bool = os.getenv('ENABLE_STRUCTURED_FILE', 'true').lower() == 'true'  # Default to true
    structured_log_file: str | None = os.getenv('STRUCTURED_LOG_FILE', '/var/log/radius_healthcheck_daemon_structured.json')

    # GCP and routing regions
    gcp_project: str | None = os.getenv('GCP_PROJECT')
    gcp_credentials: str | None = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

    # GCP Authentication Mode
    # When true, uses Workload Identity Federation / Application Default Credentials
    # When false (default), uses service account key file (GOOGLE_APPLICATION_CREDENTIALS)
    use_workload_identity: bool = os.getenv('USE_WORKLOAD_IDENTITY', 'false').lower() == 'true'

    local_region: str | None = os.getenv('LOCAL_GCP_REGION')
    remote_region: str | None = os.getenv('REMOTE_GCP_REGION')
    local_bgp_router: str | None = os.getenv('LOCAL_BGP_ROUTER')
    remote_bgp_router: str | None = os.getenv('REMOTE_BGP_ROUTER')
    local_bgp_region: str | None = os.getenv('LOCAL_BGP_REGION')
    remote_bgp_region: str | None = os.getenv('REMOTE_BGP_REGION')
    bgp_peer_project: str | None = os.getenv('BGP_PEER_PROJECT')

    # Network prefix configuration
    primary_prefix: str | None = os.getenv('PRIMARY_PREFIX')
    secondary_prefix: str | None = os.getenv('SECONDARY_PREFIX')
    primary_internal_ip: str | None = os.getenv('PRIMARY_INTERNAL_IP')
    secondary_internal_ip: str | None = os.getenv('SECONDARY_INTERNAL_IP')

    # Cloudflare API details
    cf_account_id: str | None = os.getenv('CLOUDFLARE_ACCOUNT_ID')
    cf_api_token: str | None = os.getenv('CLOUDFLARE_API_TOKEN')
    cf_desc_substring: str | None = os.getenv('DESCRIPTION_SUBSTRING')
    cf_primary_priority: int = int(os.getenv('CLOUDFLARE_PRIMARY_PRIORITY', 100))
    cf_secondary_priority: int = int(os.getenv('CLOUDFLARE_SECONDARY_PRIORITY', 200))

    # Control loop and retry settings
    check_interval: int = int(os.getenv('CHECK_INTERVAL_SECONDS', 60))
    # Per-service retry configuration for different operation types
    max_retries_health_check: int = int(os.getenv('MAX_RETRIES_HEALTH_CHECK', 5))  # Health checks are read-only, can retry more
    max_retries_bgp_check: int = int(os.getenv('MAX_RETRIES_BGP_CHECK', 4))       # BGP status checks
    max_retries_bgp_update: int = int(os.getenv('MAX_RETRIES_BGP_UPDATE', 2))     # BGP updates modify state, retry less
    max_retries_cloudflare: int = int(os.getenv('MAX_RETRIES_CLOUDFLARE', 3))     # Cloudflare updates
    max_retries: int = int(os.getenv('MAX_RETRIES', 3))  # Legacy fallback, kept for backward compatibility
    initial_backoff: float = float(os.getenv('INITIAL_BACKOFF_SECONDS', 1.0))
    max_backoff: float = float(os.getenv('MAX_BACKOFF_SECONDS', 60.0))
    cb_threshold: int = int(os.getenv('CIRCUIT_BREAKER_THRESHOLD', 5))
    cb_timeout: int = int(os.getenv('CIRCUIT_BREAKER_TIMEOUT_SECONDS', 300))

    # Passive mode - run daemon but skip all route updates
    run_passive: bool = os.getenv('RUN_PASSIVE', 'false').lower() == 'true'

    # State verification thresholds - require N consecutive detections before acting
    state_2_verification_threshold: int = int(os.getenv('STATE_2_VERIFICATION_THRESHOLD', 2))
    state_3_verification_threshold: int = int(os.getenv('STATE_3_VERIFICATION_THRESHOLD', 2))
    state_4_verification_threshold: int = int(os.getenv('STATE_4_VERIFICATION_THRESHOLD', 2))

    # Health check hysteresis - smooth out transient failures
    health_check_window: int = int(os.getenv('HEALTH_CHECK_WINDOW', 5))
    health_check_threshold: int = int(os.getenv('HEALTH_CHECK_THRESHOLD', 3))
    asymmetric_hysteresis: bool = os.getenv('ASYMMETRIC_HYSTERESIS', 'false').lower() == 'true'

    # State stability - minimum time in state before allowing transitions
    min_state_dwell_time: int = int(os.getenv('MIN_STATE_DWELL_TIME', 120))

    def __post_init__(self):
        """Parse list-based configuration after initialization."""
        # Parse dwell time exception states from comma-separated string
        exception_states_str = os.getenv('DWELL_TIME_EXCEPTION_STATES', '1,4')
        self.dwell_time_exception_states = [int(s.strip()) for s in exception_states_str.split(',')]

    # API Timeout Configuration (seconds) - adjust based on network latency and deployment environment
    gcp_api_timeout: int = int(os.getenv('GCP_API_TIMEOUT', 30))
    gcp_backend_health_timeout: int = int(os.getenv('GCP_BACKEND_HEALTH_TIMEOUT', 45))
    gcp_bgp_operation_timeout: int = int(os.getenv('GCP_BGP_OPERATION_TIMEOUT', 60))
    cloudflare_api_timeout: int = int(os.getenv('CLOUDFLARE_API_TIMEOUT', 10))
    cloudflare_bulk_timeout: int = int(os.getenv('CLOUDFLARE_BULK_TIMEOUT', 60))


# List of required environment variables (presence-only validation)
# GOOGLE_APPLICATION_CREDENTIALS is conditionally required (checked in validate_configuration)
REQUIRED_VARS_BASE = [
    'GCP_PROJECT', 'LOCAL_GCP_REGION', 'REMOTE_GCP_REGION',
    'LOCAL_BGP_ROUTER', 'REMOTE_BGP_ROUTER', 'LOCAL_BGP_REGION', 'REMOTE_BGP_REGION',
    'BGP_PEER_PROJECT', 'PRIMARY_PREFIX', 'SECONDARY_PREFIX',
    'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN', 'DESCRIPTION_SUBSTRING'
]


def validate_configuration(cfg: Config) -> list[str]:
    """
    Validates the loaded configuration for completeness, correctness, and consistency.

    This includes:
    - Checking presence of required variables.
    - Validating authentication configuration (Workload Identity OR service account key).
    - Validating CIDR/IP formatting.
    - Validating numeric ranges.
    - Verifying GCP credentials file existence and readability (when using service account).

    Args:
        cfg (Config): Parsed and populated configuration object.

    Returns:
        list[str]: A list of human-readable error strings. Empty list means validation passed.
    """
    errors: list[str] = []

    # Presence check for base required variables
    for var in REQUIRED_VARS_BASE:
        if not os.getenv(var):
            errors.append(f"Missing required environment variable: {var}")

    # Authentication-specific validation
    # Require either Workload Identity OR service account credentials
    use_workload_identity = cfg.use_workload_identity
    has_credentials_file = cfg.gcp_credentials is not None and cfg.gcp_credentials != ''

    if not use_workload_identity and not has_credentials_file:
        errors.append(
            "GCP authentication not configured: Either set USE_WORKLOAD_IDENTITY=true "
            "to use Workload Identity/Application Default Credentials, or set "
            "GOOGLE_APPLICATION_CREDENTIALS to the path of a service account key file."
        )

    # Validate IP prefix formats (CIDR blocks)
    for name in ['PRIMARY_PREFIX', 'SECONDARY_PREFIX']:
        val = os.getenv(name)
        if val:
            try:
                ipaddress.ip_network(val, strict=False)
            except Exception as e:
                errors.append(f"Invalid {name} format: {e}")

    # Validate internal IPs
    for name in ['PRIMARY_INTERNAL_IP', 'SECONDARY_INTERNAL_IP']:
        val = os.getenv(name)
        if val:
            try:
                ipaddress.ip_address(val)
            except Exception as e:
                errors.append(f"Invalid {name} format: {e}")

    # Validate numerical ranges for environment-provided numbers
    numeric_ranges = {
        'CHECK_INTERVAL_SECONDS': (1, 3600),
        'CLOUDFLARE_PRIMARY_PRIORITY': (1, 1000),
        'CLOUDFLARE_SECONDARY_PRIORITY': (1, 1000),
        'MAX_RETRIES': (1, 10),
        'MAX_RETRIES_HEALTH_CHECK': (1, 10),
        'MAX_RETRIES_BGP_CHECK': (1, 10),
        'MAX_RETRIES_BGP_UPDATE': (1, 10),
        'MAX_RETRIES_CLOUDFLARE': (1, 10),
        'INITIAL_BACKOFF_SECONDS': (0.1, 60),
        'MAX_BACKOFF_SECONDS': (1, 600),
        'CIRCUIT_BREAKER_THRESHOLD': (1, 20),
        'CIRCUIT_BREAKER_TIMEOUT_SECONDS': (30, 3600),
        'LOG_MAX_BYTES': (1024, 1073741824),  # 1 KB to 1 GB
        'LOG_BACKUP_COUNT': (1, 100),
        'STATE_2_VERIFICATION_THRESHOLD': (1, 10),
        'STATE_3_VERIFICATION_THRESHOLD': (1, 10),
        'STATE_4_VERIFICATION_THRESHOLD': (1, 10),
        'HEALTH_CHECK_WINDOW': (3, 10),
        'HEALTH_CHECK_THRESHOLD': (1, 10),
        'MIN_STATE_DWELL_TIME': (30, 600),
        'GCP_API_TIMEOUT': (5, 300),
        'GCP_BACKEND_HEALTH_TIMEOUT': (5, 300),
        'GCP_BGP_OPERATION_TIMEOUT': (5, 300),
        'CLOUDFLARE_API_TIMEOUT': (5, 300),
        'CLOUDFLARE_BULK_TIMEOUT': (5, 300),
    }

    for var, (mn, mx) in numeric_ranges.items():
        raw = os.getenv(var)
        if raw:
            try:
                val = float(raw)
                if val < mn or val > mx:
                    errors.append(f"{var} must be between {mn} and {mx}, got {val}")
            except ValueError:
                errors.append(f"{var} must be numeric, got '{raw}'")

    # Validate constraint: health_check_threshold must be less than health_check_window
    if cfg.health_check_threshold >= cfg.health_check_window:
        errors.append(f"HEALTH_CHECK_THRESHOLD ({cfg.health_check_threshold}) must be less than "
                     f"HEALTH_CHECK_WINDOW ({cfg.health_check_window})")

    # GCP credential file existence & readability (only when using service account key mode)
    if not cfg.use_workload_identity:
        creds = cfg.gcp_credentials
        if creds and not os.path.isfile(creds):
            errors.append(f"GCP credentials file not found: {creds}")
        elif creds and not os.access(creds, os.R_OK):
            errors.append(f"GCP credentials file not readable: {creds}")

    # Validate structured log file path if enabled
    if cfg.enable_structured_file and cfg.structured_log_file:
        structured_log_dir = os.path.dirname(cfg.structured_log_file)
        if structured_log_dir and not os.path.exists(structured_log_dir):
            # Try to create the directory
            try:
                os.makedirs(structured_log_dir, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create structured log directory {structured_log_dir}: {e}")

    return errors
