import sys, logging
from .config import Config
from .logging_setup import setup_logger
from .daemon import startup, run_loop

def main():

    cfg = Config()
    
    # Enhanced logger setup with structured logging support
    logger = setup_logger(
        name=cfg.logger_name,
        level=cfg.log_level,
        log_file=cfg.log_file,
        max_bytes=cfg.log_max_bytes,
        backup_count=cfg.log_backup_count,
        # Add the new structured logging parameters
        enable_structured_console=cfg.enable_structured_console,
        enable_structured_file=cfg.enable_structured_file,
        structured_log_file=cfg.structured_log_file
    )

    if cfg.enable_gcp_logging:
        try:
            import google.cloud.logging
            from google.cloud.logging.handlers import CloudLoggingHandler
            client = google.cloud.logging.Client()
            cloud_handler = CloudLoggingHandler(client, name="radius_healthcheck_daemon")
            cloud_handler.setLevel(getattr(logging, cfg.log_level, logging.INFO))
            logger.addHandler(cloud_handler)
            logger.info("Google Cloud Logging handler enabled.")
        except Exception as e:
            logger.warning(f"Could not enable Google Cloud Logging: {e}")

    try:
        compute = startup(cfg)
        run_loop(cfg, compute)
    except SystemExit as e:
        sys.exit(e.code if isinstance(e.code,int) else 1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.critical(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        for h in logger.handlers:
            try:
                h.flush()
            except Exception:
                pass
        sys.exit(0)

if __name__ == "__main__":
    main()
