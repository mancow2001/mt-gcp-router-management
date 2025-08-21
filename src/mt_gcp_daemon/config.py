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
            - max_retries: Retry attempts on failure.
            - initial_backoff / max_backoff: Exponential backoff timing.
            - cb_threshold / cb_timeout: Circuit breaker limits.
    """
    # Logging
    logger_name: str = os.getenv('LOGGER_NAME', 'HEALTH_CHECK_DAEMON').upper()
    log_level: str = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_file: str | None = os.getenv('LOG_FILE', '/var/log/radius_healthcheck_daemon.log')
    log_max_bytes: int = int(os.getenv('LOG_MAX_BYTES', 10 * 1024 * 1024))
    log_backup_count: int = int(os.getenv('LOG_BACKUP_COUNT', 5))
    enable_gcp_logging: bool = os.getenv('ENABLE_GCP_LOGGING', 'false').lower() == 'true'

    # GCP and routing regions
    gcp_project: str | None = os.getenv('GCP_PROJECT')
    gcp_credentials: str | None = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
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
    max_retries: int = int(os.getenv('MAX_RETRIES', 3))
    initial_backoff: float = float(os.getenv('INITIAL_BACKOFF_SECONDS', 1.0))
    max_backoff: float = float(os.getenv('MAX_BACKOFF_SECONDS', 60.0))
    cb_threshold: int = int(os.getenv('CIRCUIT_BREAKER_THRESHOLD', 5))
    cb_timeout: int = int(os.getenv('CIRCUIT_BREAKER_TIMEOUT_SECONDS', 300))


# List of required environment variables (presence-only validation)
REQUIRED_VARS = [
    'GCP_PROJECT', 'GOOGLE_APPLICATION_CREDENTIALS', 'LOCAL_GCP_REGION', 'REMOTE_GCP_REGION',
    'LOCAL_BGP_ROUTER', 'REMOTE_BGP_ROUTER', 'LOCAL_BGP_REGION', 'REMOTE_BGP_REGION',
    'BGP_PEER_PROJECT', 'PRIMARY_PREFIX', 'SECONDARY_PREFIX',
    'CLOUDFLARE_ACCOUNT_ID', 'CLOUDFLARE_API_TOKEN', 'DESCRIPTION_SUBSTRING'
]


def validate_configuration(cfg: Config) -> list[str]:
    """
    Validates the loaded configuration for completeness, correctness, and consistency.

    This includes:
    - Checking presence of required variables.
    - Validating CIDR/IP formatting.
    - Validating numeric ranges.
    - Verifying GCP credentials file existence and readability.

    Args:
        cfg (Config): Parsed and populated configuration object.

    Returns:
        list[str]: A list of human-readable error strings. Empty list means validation passed.
    """
    errors: list[str] = []

    # Presence check
    for var in REQUIRED_VARS:
        if not os.getenv(var):
            errors.append(f"Missing required environment variable: {var}")

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
        'INITIAL_BACKOFF_SECONDS': (0.1, 60),
        'MAX_BACKOFF_SECONDS': (1, 600),
        'CIRCUIT_BREAKER_THRESHOLD': (1, 20),
        'CIRCUIT_BREAKER_TIMEOUT_SECONDS': (30, 3600),
        'LOG_MAX_BYTES': (1024, 1073741824),  # 1 KB to 1 GB
        'LOG_BACKUP_COUNT': (1, 100),
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

    # GCP credential file existence & readability
    creds = cfg.gcp_credentials
    if creds and not os.path.isfile(creds):
        errors.append(f"GCP credentials file not found: {creds}")
    elif creds and not os.access(creds, os.R_OK):
        errors.append(f"GCP credentials file not readable: {creds}")

    return errors
