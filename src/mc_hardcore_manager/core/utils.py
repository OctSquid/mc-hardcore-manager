# Utility functions for the application can be placed here.
import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(log_dir: str = "logs", log_file: str = "bot.log", level=logging.INFO):
    """Sets up logging configuration."""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_path = os.path.join(log_dir, log_file)

    # Basic configuration
    logging.basicConfig(
        level=level,
        format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        handlers=[
            RotatingFileHandler(log_path, maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'),
            logging.StreamHandler() # Also log to console
        ]
    )

    # Set higher level for noisy libraries if needed
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)

    logging.info("Logging setup complete.")
