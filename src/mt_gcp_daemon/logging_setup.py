import os
import logging
from logging.handlers import RotatingFileHandler

def setup_logger(name: str, level: str, log_file: str | None, max_bytes: int, backup_count: int):
    """
    Configures and returns a logger with the specified parameters.

    Supports both console logging and optional rotating file logging.
    
    Args:
        name (str): The name of the logger.
        level (str): Logging level name (e.g., 'DEBUG', 'INFO', 'WARNING').
        log_file (str | None): Path to a log file. If None, file logging is skipped.
        max_bytes (int): Maximum file size in bytes before log rotation occurs.
        backup_count (int): Number of rotated log files to keep.

    Returns:
        logging.Logger: Configured logger instance.
    """
    
    # Create or retrieve a logger instance by name
    logger_name = name or os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON")
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level, logging.INFO))  # Set log level from string, default to INFO

    # Create a log message formatter
    formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')

    # Set up console (stream) logging
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, level, logging.INFO))  # Apply same log level to stream handler
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Optionally set up rotating file logging
    if log_file:
        try:
            # Ensure the directory for the log file exists
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            # Create a rotating file handler
            fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
            fh.setLevel(getattr(logging, level, logging.INFO))  # Apply log level to file handler
            fh.setFormatter(formatter)
            logger.addHandler(fh)

            logger.info(f"File logging enabled: {log_file}")
        except Exception as e:
            # Log a warning if file handler setup fails
            logger.warning(f"Could not setup file logging at {log_file}: {e}")

    return logger
