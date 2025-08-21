import sys, logging
from .config import Config
from .logging_setup import setup_logger
from .daemon import startup, run_loop

def main():

    cfg = Config()
    logger = setup_logger(cfg.logger_name, cfg.log_level, cfg.log_file, cfg.log_max_bytes, cfg.log_backup_count)

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
