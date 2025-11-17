import os
import json
import logging
from logging.handlers import RotatingFileHandler

class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that outputs JSON for structured logging.
    """
    def format(self, record):
        # Check if this is a structured log entry
        if hasattr(record, 'json_fields') and record.json_fields.get('structured_event'):
            # Output pure JSON for structured events
            return json.dumps(record.json_fields, separators=(',', ':'))
        else:
            # Use standard formatting for regular log messages
            return super().format(record)

class StructuredJSONFormatter(logging.Formatter):
    """
    Custom formatter that outputs properly formatted JSON array.
    """
    def format(self, record):
        # Check if this is a structured log entry
        if hasattr(record, 'json_fields') and record.json_fields.get('structured_event'):
            # Output JSON with proper formatting and indentation
            return json.dumps(record.json_fields, indent=2, separators=(',', ': '))
        else:
            # Use standard formatting for regular log messages
            return super().format(record)

class StructuredArrayHandler(RotatingFileHandler):
    """
    Custom handler that maintains a proper JSON array structure in the log file.
    """
    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0, encoding=None, delay=False):
        super().__init__(filename, mode, maxBytes, backupCount, encoding, delay)
        self.first_record = True
        self._initialize_file()
    
    def _initialize_file(self):
        """Initialize the file with proper JSON array structure if it's empty."""
        try:
            # Check if file exists and has content
            if os.path.exists(self.baseFilename):
                with open(self.baseFilename, 'r') as f:
                    content = f.read().strip()
                    if content and not content.startswith('['):
                        # File has content but isn't a JSON array, backup and restart
                        backup_name = f"{self.baseFilename}.backup"
                        os.rename(self.baseFilename, backup_name)
                        self.first_record = True
                    elif content.startswith('['):
                        # File is already a JSON array, we'll append to it
                        self.first_record = False
                    else:
                        # File is empty
                        self.first_record = True
            else:
                self.first_record = True
                
            # If this is a new file, start the JSON array
            if self.first_record:
                with open(self.baseFilename, 'w') as f:
                    f.write('[\n')
                    
        except Exception:
            # If anything goes wrong, start fresh
            self.first_record = True
            try:
                with open(self.baseFilename, 'w') as f:
                    f.write('[\n')
            except Exception:
                pass
    
    def emit(self, record):
        """Emit a record, maintaining proper JSON array structure."""
        try:
            if self.shouldRollover(record):
                self.doRollover()
            
            # Format the record
            msg = self.format(record)
            
            # Only process if it's actually a JSON message
            if msg and msg.startswith('{'):
                # Read current file content
                current_content = ''
                try:
                    with open(self.baseFilename, 'r') as f:
                        current_content = f.read().strip()
                except Exception:
                    current_content = ''
                
                # Determine if we need to add a comma
                if current_content and not current_content.endswith('['):
                    prefix = ',\n'
                else:
                    prefix = ''
                
                # Write the new record
                with open(self.baseFilename, 'a') as f:
                    f.write(f"{prefix}{msg}")
                    f.flush()
                    
        except Exception:
            self.handleError(record)
    
    def close(self):
        """Close the handler and properly terminate the JSON array."""
        try:
            # Close the JSON array
            with open(self.baseFilename, 'a') as f:
                f.write('\n]')
                f.flush()
        except Exception:
            pass
        super().close()

class StructuredFilter(logging.Filter):
    """
    Filter that only allows structured log events to pass through.
    """
    def filter(self, record):
        # Only allow records that have json_fields with structured_event=True
        return (hasattr(record, 'json_fields') and
                record.json_fields.get('structured_event', False))

class NonStructuredFilter(logging.Filter):
    """
    Filter that only allows non-structured log events to pass through.
    """
    def filter(self, record):
        # Only allow records that DON'T have structured_event=True
        return not (hasattr(record, 'json_fields') and
                   record.json_fields.get('structured_event', False))

def setup_logger(name: str, level: str, log_file: str | None, max_bytes: int, backup_count: int,
                enable_structured_console: bool = False, enable_structured_file: bool = False,
                structured_log_file: str | None = None):
    """
    Enhanced logger setup with structured logging options and proper JSON formatting.
    
    Args:
        enable_structured_console (bool): Output JSON to console for structured events
        enable_structured_file (bool): Output JSON to separate structured log file
        structured_log_file (str): Path to structured JSON log file
    """
    
    # Create or retrieve a logger instance by name
    logger_name = name or os.getenv("LOGGER_NAME", "HEALTH_CHECK_DAEMON")
    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, level, logging.INFO))

    # Regular formatter for human-readable logs
    regular_formatter = logging.Formatter('%(asctime)s %(levelname)s [%(name)s] %(message)s')
    
    # Structured formatter for JSON output
    structured_formatter = StructuredFormatter()
    structured_json_formatter = StructuredJSONFormatter()

    # Set up console (stream) logging
    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, level, logging.INFO))
    
    if enable_structured_console:
        # Console shows structured JSON for structured events only
        ch.setFormatter(structured_formatter)
        ch.addFilter(StructuredFilter())
        logger.info("Console structured logging enabled (JSON output for structured events only)")
    else:
        # Console shows human-readable format for non-structured events only
        ch.setFormatter(regular_formatter)
        ch.addFilter(NonStructuredFilter())
    
    logger.addHandler(ch)

    # Set up regular file logging (non-structured events only)
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            fh = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count)
            fh.setLevel(getattr(logging, level, logging.INFO))
            fh.setFormatter(regular_formatter)
            fh.addFilter(NonStructuredFilter())  # Only non-structured events
            logger.addHandler(fh)
            logger.info(f"Regular file logging enabled: {log_file}")
        except Exception as e:
            logger.warning(f"Could not setup regular file logging at {log_file}: {e}")

    # Set up structured JSON file logging (structured events only)
    if enable_structured_file and structured_log_file:
        try:
            os.makedirs(os.path.dirname(structured_log_file), exist_ok=True)
            sfh = StructuredArrayHandler(structured_log_file, maxBytes=max_bytes, backupCount=backup_count)
            sfh.setLevel(getattr(logging, level, logging.INFO))
            sfh.setFormatter(structured_json_formatter)
            sfh.addFilter(StructuredFilter())  # Only structured events
            logger.addHandler(sfh)
            logger.info(f"Structured JSON file logging enabled: {structured_log_file}")
        except Exception as e:
            logger.warning(f"Could not setup structured file logging at {structured_log_file}: {e}")

    return logger
