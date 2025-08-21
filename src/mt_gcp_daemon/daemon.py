import time, threading, logging, signal, sys
from .config import Config, validate_configuration
from .logging_setup import setup_logger
from .circuit import CircuitBreaker, exponential_backoff_retry
from . import gcp as gcp_mod
from . import cloudflare as cf_mod
from .state import determine_state_code, STATE_ACTIONS
from dotenv import load_dotenv
import os

# Load environment variables from a .env file into the runtime environment
load_dotenv()

# Event used to signal graceful shutdown
shutdown_event = threading.Event()

def signal_handler(signum, frame):
    """
    Signal handler to catch SIGTERM/SIGINT and initiate shutdown.
    """
    logger = logging.getLogger("healthcheck-daemon")
    name = {signal.SIGTERM:'SIGTERM', signal.SIGINT:'SIGINT'}.get(signum, str(signum))
    logger.info(f"Received {name}, shutting down...")
    shutdown_event.set()

def setup_signal_handlers():
    """
    Register handlers for termination signals to allow clean shutdown.
    """
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

def run_loop(cfg: Config, compute):
    """
    Main control loop that performs:
      - Health check on GCP backend services
      - BGP session status check
      - BGP advertisement management
      - Cloudflare route prioritization

    Uses circuit breakers to isolate failures and exponential backoff to retry failures.

    Args:
        cfg (Config): Parsed runtime configuration.
        compute: Authorized GCP Compute Engine client.
    """
    logger = logging.getLogger(os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON"))
    logger.info(f"Daemon starting with {cfg.check_interval}s interval.")
    logger.info(f"Local region: {cfg.local_region}, Remote region: {cfg.remote_region}")
    logger.info(f"Primary prefix: {cfg.primary_prefix}, Secondary prefix: {cfg.secondary_prefix}")

    # Dedicated circuit breakers per service type
    circuit_breakers = {
        'gcp_health': CircuitBreaker(cfg.cb_threshold, cfg.cb_timeout),
        'gcp_bgp': CircuitBreaker(cfg.cb_threshold, cfg.cb_timeout),
        'gcp_advertisement': CircuitBreaker(cfg.cb_threshold, cfg.cb_timeout),
        'cloudflare': CircuitBreaker(cfg.cb_threshold, cfg.cb_timeout),
    }

    consecutive_errors = 0
    max_consecutive_errors = 10

    while not shutdown_event.is_set():
        try:
            loop_start = time.time()

            # ─── GCP Backend Health Checks ─────────────────────────────────────────────
            local_healthy = circuit_breakers['gcp_health'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.backend_services_healthy(cfg.gcp_project, cfg.local_region, compute),
                    cfg.max_retries, cfg.initial_backoff, cfg.max_backoff
                )
            )

            remote_healthy = circuit_breakers['gcp_health'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.backend_services_healthy(cfg.gcp_project, cfg.remote_region, compute),
                    cfg.max_retries, cfg.initial_backoff, cfg.max_backoff
                )
            )

            # ─── BGP Session Health Check ──────────────────────────────────────────────
            remote_bgp_up, remote_peer_statuses = circuit_breakers['gcp_bgp'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.router_bgp_sessions_healthy(cfg.bgp_peer_project, cfg.remote_bgp_region, cfg.remote_bgp_router, compute),
                    cfg.max_retries, cfg.initial_backoff, cfg.max_backoff
                )
            )

            logger.info(f"Health Status - Local: {local_healthy}, Remote: {remote_healthy}, Remote BGP: {remote_bgp_up}")

            # ─── Determine Routing State ───────────────────────────────────────────────
            state_code = determine_state_code(local_healthy, remote_healthy, remote_bgp_up)
            advertise_primary, advertise_secondary = STATE_ACTIONS.get(state_code, (False, False))
            logger.info(f"State {state_code} -> Plan Primary={advertise_primary} Secondary={advertise_secondary}")

            # ─── Update BGP Advertisements ─────────────────────────────────────────────
            primary_success = circuit_breakers['gcp_advertisement'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.update_bgp_advertisement(cfg.bgp_peer_project, cfg.local_bgp_region, cfg.local_bgp_router,
                                                     cfg.primary_prefix, compute, advertise=advertise_primary),
                    cfg.max_retries, cfg.initial_backoff, cfg.max_backoff
                )
            )

            secondary_success = circuit_breakers['gcp_advertisement'].call(
                lambda: exponential_backoff_retry(
                    gcp_mod.update_bgp_advertisement(cfg.bgp_peer_project, cfg.remote_bgp_region, cfg.remote_bgp_router,
                                                     cfg.secondary_prefix, compute, advertise=advertise_secondary),
                    cfg.max_retries, cfg.initial_backoff, cfg.max_backoff
                )
            )

            # ─── Adjust Cloudflare Route Priorities ────────────────────────────────────
            desired_priority = cfg.cf_primary_priority if local_healthy else cfg.cf_secondary_priority
            cloudflare_success = circuit_breakers['cloudflare'].call(
                lambda: exponential_backoff_retry(
                    lambda: cf_mod.update_routes_by_description_bulk(cfg.cf_account_id, cfg.cf_api_token,
                                                                     cfg.cf_desc_substring, desired_priority),
                    cfg.max_retries, cfg.initial_backoff, cfg.max_backoff
                )
            )

            # ─── Status Logging ────────────────────────────────────────────────────────
            if primary_success and secondary_success and cloudflare_success:
                consecutive_errors = 0
                logger.debug("All operations completed successfully")
            else:
                consecutive_errors += 1
                logger.warning(f"Operation failures ({consecutive_errors}/10)")

            # ─── Sleep Until Next Check ────────────────────────────────────────────────
            loop_duration = time.time() - loop_start
            sleep_time = max(0, cfg.check_interval - loop_duration)
            if shutdown_event.wait(sleep_time):
                break

        except Exception as e:
            consecutive_errors += 1
            logger.exception(f"Unexpected error in main loop (attempt {consecutive_errors}): {e}")
            if consecutive_errors >= max_consecutive_errors:
                logger.critical("Too many consecutive errors; exiting.")
                break
            if shutdown_event.wait(min(cfg.check_interval, 30)):
                break

    logger.info("Main loop exited. Cleanup complete.")

def startup(cfg: Config):
    """
    Bootstraps the environment before the main daemon loop starts.

    - Validates configuration and exits if invalid
    - Initializes GCP and Cloudflare connections
    - Registers signal handlers

    Args:
        cfg (Config): Fully parsed config object

    Returns:
        compute: Initialized GCP Compute client for use in run_loop()
    """
    logger = logging.getLogger("healthcheck-daemon")

    # Validate config structure and environment
    errors = validate_configuration(cfg)
    if errors:
        logger.error("Configuration validation failed:")
        for e in errors:
            logger.error(f" - {e}")
        raise SystemExit(1)

    # Initialize and test GCP connectivity
    compute = gcp_mod.build_compute_client(cfg.gcp_credentials)
    gcp_mod.validate_gcp_connectivity(cfg.gcp_project, [cfg.local_region, cfg.remote_region], compute)

    # Validate Cloudflare credentials and permissions
    try:
        cf_mod.validate_cloudflare_connectivity(cfg.cf_account_id, cfg.cf_api_token)
    except Exception as e:
        logger.error(f"Cloudflare connectivity validation failed: {e}")
        raise

    # Setup OS signal handling
    setup_signal_handlers()

    return compute
