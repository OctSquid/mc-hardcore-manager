import threading
import logging
import asyncio
import subprocess
import re
from typing import Callable, Optional, Coroutine, Any, Tuple, Union, Dict, List
from datetime import datetime, timezone

# 死亡メッセージ検出のためのモジュールをインポート
from .death_patterns import detect_death_message

logger = logging.getLogger(__name__)

# RCONの準備完了を示すログパターン
RCON_READY_PATTERN = re.compile(r'RCON running on .+:\d+')

class LogMonitor:
    """Monitors the stdout and stderr streams of a subprocess in separate threads."""

    def __init__(
        self,
        process: subprocess.Popen,
        loop: asyncio.AbstractEventLoop,
        death_handler_fn: Optional[Callable[[str, str, str], Coroutine[Any, Any, None]]] = None,
        rcon_ready_callback: Optional[Callable[[], Coroutine[Any, Any, None]]] = None
    ):
        """
        Initializes the LogMonitor.

        Args:
            process: The subprocess.Popen object to monitor.
            loop: The asyncio event loop to run callbacks in.
            death_handler_fn: Function to call directly when a player death is detected.
                             This should be DeathHandler.handle_death or similar.
        """
        if not process.stdout or not process.stderr:
            raise ValueError("Process stdout and stderr must be piped.")

        self.process = process
        self.loop = loop
        self.death_handler_fn = death_handler_fn
        self.rcon_ready_callback = rcon_ready_callback
        self.rcon_ready_triggered = False  # RCONコールバックがすでに呼び出されたかどうか
        self.stop_event = threading.Event()
        self.stdout_thread: Optional[threading.Thread] = None
        self.stderr_thread: Optional[threading.Thread] = None
        self._threads_started = False

    def start(self):
        """Starts the monitoring threads."""
        if self._threads_started:
            logger.warning("Log monitoring threads already started.")
            return

        if not self.process or self.process.poll() is not None:
             logger.error("Cannot start log monitoring: Process is not running.")
             return

        self.stop_event.clear()
        logger.info(f"Starting log monitoring for PID: {self.process.pid}")

        self.stdout_thread = threading.Thread(
            target=self._stream_reader,
            args=(self.process.stdout, "[Server STDOUT]"),
            daemon=True
        )
        self.stderr_thread = threading.Thread(
            target=self._stream_reader,
            args=(self.process.stderr, "[Server STDERR]"),
            daemon=True
        )

        self.stdout_thread.start()
        self.stderr_thread.start()
        self._threads_started = True
        logger.info("Log monitoring threads started.")

    def stop(self):
        """Signals the monitoring threads to stop and waits for them to join."""
        if not self._threads_started:
            logger.info("Log monitoring threads were not running.")
            return

        logger.info("Stopping log monitoring threads...")
        self.stop_event.set()

        # Wait for threads to finish
        if self.stdout_thread and self.stdout_thread.is_alive():
            logger.debug("Waiting for stdout thread to join...")
            self.stdout_thread.join(timeout=5)
            if self.stdout_thread.is_alive():
                 logger.warning("Stdout monitoring thread did not finish cleanly.")
        if self.stderr_thread and self.stderr_thread.is_alive():
            logger.debug("Waiting for stderr thread to join...")
            self.stderr_thread.join(timeout=5)
            if self.stderr_thread.is_alive():
                 logger.warning("Stderr monitoring thread did not finish cleanly.")

        self._threads_started = False
        logger.info("Log monitoring threads stopped.")

    def _stream_reader(self, stream, prefix: str):
        """Reads lines from a stream, logs them, and processes death events."""
        try:
            # Use iter(stream.readline, b'') to read line by line
            for line in iter(stream.readline, b''):
                if self.stop_event.is_set():
                    logger.debug(f"{prefix} stop event received, exiting reader.")
                    break
                try:
                    log_line = line.decode('utf-8', errors='replace').strip()
                    if log_line: # Avoid logging empty lines
                        logger.info(f"{prefix} {log_line}") # Log the raw line

                    if prefix == "[Server STDOUT]":
                        # --- RCON準備完了メッセージの検出 ---
                        if self.rcon_ready_callback and not self.rcon_ready_triggered:
                            # RCONの準備完了メッセージを検出
                            if RCON_READY_PATTERN.search(log_line):
                                logger.info("RCON server is ready for connection!")
                                # コールバックを一度だけ実行するようにフラグをセット
                                self.rcon_ready_triggered = True
                                # RCON準備完了コールバックを非同期で呼び出す
                                asyncio.run_coroutine_threadsafe(
                                    self.rcon_ready_callback(),
                                    self.loop
                                )
                                
                        # --- Call death handler if set ---
                        if self.death_handler_fn:
                            # 死亡メッセージの検出
                            death_info = detect_death_message(log_line)
                            if death_info:
                                player_name = death_info["player_name"]
                                timestamp = death_info["timestamp"]
                                full_message = death_info["full_message"]
                                
                                logger.info(f"Death detected: Player {player_name} at {timestamp}")
                                
                                # デスハンドラーを非同期で呼び出す
                                asyncio.run_coroutine_threadsafe(
                                    self.death_handler_fn(player_name, full_message, timestamp),
                                    self.loop
                                )
                except UnicodeDecodeError as ude:
                     logger.warning(f"{prefix} Decoding error: {ude}. Raw: {line!r}") # Log raw bytes if decoding fails
                except Exception as e:
                    # Catch errors during line processing but continue reading
                    logger.error(f"Error processing log line in {prefix}: {e}", exc_info=True)

            logger.info(f"{prefix} stream ended or reader stopped.")
        except Exception as e:
             # Catch errors related to the stream reading itself
             logger.error(f"Exception in stream reader ({prefix}): {e}", exc_info=True)
        finally:
            # Stream closing is typically handled by Popen when the process ends
            logger.debug(f"{prefix} reader thread finished.")
