import subprocess
import logging
import os
import asyncio
import time # Import time for synchronous sleep
from typing import Optional, Callable, Coroutine, Any, cast

# Import new components and exceptions
from ..config import Config # Use the Config model
from .rcon_client import RconClient, RconError
from ..core.exceptions import ServerProcessError

# Forward declaration for type hinting if LogMonitor is used here
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .log_monitor import LogMonitor

logger = logging.getLogger(__name__)

# 型を定義
DeathHandlerType = Callable[[str, str, str], Coroutine[Any, Any, None]]

class ServerProcessManager:
    """Handles the starting, stopping, and monitoring of the Minecraft server subprocess."""

    def __init__(self, config: Config, rcon_client: RconClient, data_manager=None): # データマネージャーをオプションで追加
        self.config = config # Store the full Config object
        self.rcon_client = rcon_client
        self.data_manager = data_manager # データマネージャーを保存
        self.process: Optional[subprocess.Popen] = None
        # Keep log_monitor reference separate, managed externally perhaps
        # self._log_monitor: Optional['LogMonitor'] = None

    def is_running(self) -> bool:
        """Checks if the server process is currently running."""
        return self.process is not None and self.process.poll() is None

    def get_pid(self) -> Optional[int]:
        """Returns the PID of the running server process, or None."""
        if self.process is None or not self.is_running():
            return None
        return self.process.pid

    async def start(self) -> tuple[subprocess.Popen, 'LogMonitor']: # Make start async
        """
        Starts the Minecraft server process asynchronously.

        Returns:
            The Popen object for the running process.
        Raises:
            ServerProcessError: If the server is already running or fails to start.
        """
        # データマネージャーがあれば開始時間を更新
        if hasattr(self, 'data_manager') and self.data_manager:
            self.data_manager._update_start_time()
            logger.info("サーバー起動時にチャレンジ開始時間を更新しました")
        if self.is_running():
            pid = self.get_pid()
            msg = f"Start called but server process (PID: {pid}) is already running."
            logger.warning(msg)
            # Instead of returning the process, raise an error or return None consistently
            raise ServerProcessError(msg) # Or return self.process if preferred

        # Use validated config paths
        server_script_path = self.config.server.script
        server_script = str(server_script_path.resolve()) # Get absolute path string
        server_dir = str(server_script_path.parent.resolve()) # Get directory path string

        if not server_script_path.exists():
             err_msg = f"Server script not found: {server_script}"
             logger.error(err_msg)
             raise ServerProcessError(err_msg)
        if not server_script_path.parent.is_dir():
             err_msg = f"Server script directory not found: {server_dir}"
             logger.error(err_msg)
             raise ServerProcessError(err_msg)

        try:
            logger.info(f"Attempting to start Minecraft server using script: '{server_script}' in directory: '{server_dir}'")
            # Start process with shell=False for security and better control
            self.process = subprocess.Popen(
                [server_script], # Command and args as a list
                cwd=server_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,
                shell=False, # Recommended to be False
                # Set environment variables if needed, e.g., for Java memory:
                # env=os.environ.copy().update({"JVM_ARGS": "-Xmx4G -Xms1G"})
            )
            logger.info(f"Server process started with PID: {self.process.pid}")

            # Brief asynchronous pause to check for immediate failure
            await asyncio.sleep(2) # Use asyncio.sleep in async method

            if self.process.poll() is not None:
                 exit_code = self.process.poll()
                 stderr_output = ""
                 try:
                     # Try reading stderr without blocking indefinitely
                     if self.process.stderr:
                          # Set streams to non-blocking? Or read in thread?
                          # Simple approach: read available bytes
                          stderr_bytes = self.process.stderr.read()
                          stderr_output = stderr_bytes.decode('utf-8', errors='replace')
                          logger.error(f"Stderr from failed start: {stderr_output}")
                 except Exception as read_e:
                      logger.error(f"Error reading stderr from failed process: {read_e}")

                 err_msg = f"Server process failed on startup (code: {exit_code}). Stderr: {stderr_output[:500]}..."
                 logger.error(err_msg)
                 self.process = None
                 raise ServerProcessError(err_msg)

            logger.info(f"Server process (PID: {self.process.pid}) appears to have started successfully.")
            
            # Initialize LogMonitor with direct death handling
            from .log_monitor import LogMonitor
            
            # Get DeathHandler from bot instance if available
            bot_instance = getattr(self.rcon_client, 'bot', None)
            death_handler_fn: Optional[DeathHandlerType] = None
            
            if bot_instance:
                # botインスタンスにdeathのハンドラが設定されているか確認
                death_handler = getattr(bot_instance, 'death_handler', None)
                if death_handler:
                    # handle_deathメソッドを確認
                    if hasattr(death_handler, 'handle_death') and callable(death_handler.handle_death):
                        # 型キャストを使ってPylanceに正しい型を伝える
                        death_handler_fn = cast(DeathHandlerType, death_handler.handle_death)
                        logger.info(f"Using DeathHandler.handle_death function directly for death events: {death_handler_fn}")
                    else:
                        logger.warning("DeathHandler found but handle_death method is missing or not callable")
                else:
                    logger.warning("Bot instance found but death_handler is not set")
            else:
                logger.warning("Bot instance not found on RconClient, death events may not be handled properly")
            
            # RCON準備完了コールバックを定義
            async def on_rcon_ready():
                """RCONサーバーが準備完了した時のコールバック関数"""
                logger.info("RCON server reported ready. Waiting 20 seconds for stabilization before initializing scoreboard...")
                await asyncio.sleep(20) # Wait for server stabilization
                logger.info("Stabilization wait complete. Proceeding with scoreboard initialization...")

                try:
                    # スコアボードマネージャを取得
                    if bot_instance:
                        scoreboard_manager = getattr(bot_instance, 'scoreboard_manager', None)
                        data_manager = getattr(bot_instance, 'data_manager', None)
                        
                        if scoreboard_manager and data_manager:
                            # スコアボードの初期化
                            await scoreboard_manager.init_death_count_scoreboard()
                            # プレイヤーの死亡回数をスコアボードに反映
                            await scoreboard_manager.update_player_death_counts(data_manager)
                            logger.info("Scoreboard initialized after RCON became ready")
                        else:
                            logger.warning("Scoreboard/data manager not found, scoreboard not initialized")
                except Exception as e:
                    logger.error(f"Error initializing scoreboard after RCON became ready: {e}", exc_info=True)
            
            # LogMonitorにRCON準備完了コールバックを設定
            log_monitor = LogMonitor(
                self.process,
                asyncio.get_running_loop(),
                death_handler_fn,
                on_rcon_ready  # RCON準備完了コールバックを追加
            )
            log_monitor.start()
            logger.info("Log monitoring started with direct death handling")
            
            # サーバー起動時にDeathHandlerのフラグをリセット
            bot_instance = getattr(self.rcon_client, 'bot', None)
            if bot_instance:
                death_handler = getattr(bot_instance, 'death_handler', None)
                if death_handler and hasattr(death_handler, 'reset_death_action_flags'):
                    death_handler.reset_death_action_flags()
                    logger.info("Reset death action flags on server start")
            
            # Return process and log_monitor as a named tuple for better clarity
            return (self.process, log_monitor)

        except FileNotFoundError as e:
             err_msg = f"Server script '{server_script}' not found or not executable."
             logger.error(f"{err_msg}: {e}", exc_info=True)
             self.process = None
             raise ServerProcessError(err_msg) from e
        except Exception as e:
            err_msg = f"Failed to start server process: {e}"
            logger.error(err_msg, exc_info=True)
            self.process = None
            raise ServerProcessError(err_msg) from e

    async def stop(self) -> bool:
        """
        Stops the Minecraft server process, attempting graceful shutdown first via RCON.

        Returns:
            True if the process is confirmed stopped, False otherwise.
        """
        if not self.is_running() or not self.process:
            logger.info("Stop called but server process is not running or process handle is missing.")
            return True

        pid = self.process.pid
        logger.info(f"Attempting to stop Minecraft server (PID: {pid})")

        # 1. Try graceful shutdown via RCON 'stop' command
        stopped_gracefully = False
        try:
            # RconClient.connect now raises RconError on failure
            await self.rcon_client.connect()
            logger.info(f"[PID:{pid}] Sending 'stop' command via RCON...")
            # RconClient.command is now async and raises RconError
            response = await self.rcon_client.command("stop")
            logger.info(f"[PID:{pid}] RCON 'stop' command sent. Response: '{response}'. Waiting up to 30s for server process to exit...")

            try:
                # Wait for process exit using the async helper
                await asyncio.wait_for(self._wait_for_process_exit(self.process), timeout=30.0)
                stopped_gracefully = True
                logger.info(f"[PID:{pid}] Server process exited gracefully after RCON stop.")
            except asyncio.TimeoutError:
                logger.warning(f"[PID:{pid}] Server did not stop within 30s after RCON 'stop'. Proceeding to terminate.")
            except Exception as e:
                 logger.error(f"[PID:{pid}] Error waiting for server process after RCON stop: {e}", exc_info=True)

        except RconError as e:
            logger.warning(f"[PID:{pid}] RCON error during graceful shutdown attempt: {e}. Proceeding to terminate.")
        except Exception as e:
             logger.error(f"[PID:{pid}] Unexpected error during RCON shutdown attempt: {e}", exc_info=True)
        finally:
             # Ensure RCON is disconnected
             await self.rcon_client.disconnect()

        # 2. If graceful shutdown failed or wasn't attempted, terminate forcefully
        if not stopped_gracefully and self.is_running():
            logger.warning(f"[PID:{pid}] Terminating server process forcefully (SIGTERM).")
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self._wait_for_process_exit(self.process), timeout=10.0)
                    logger.info(f"[PID:{pid}] Server process terminated successfully after SIGTERM.")
                except asyncio.TimeoutError:
                    logger.warning(f"[PID:{pid}] Server process did not terminate after 10s (SIGTERM). Sending SIGKILL.")
                    self.process.kill()
                    # Short wait after kill
                    await asyncio.sleep(0.5)
                    if self.is_running(): # Check again after kill
                         logger.error(f"[PID:{pid}] Server process STILL running after SIGKILL!")
                    else:
                         logger.info(f"[PID:{pid}] Server process killed successfully.")
                except Exception as e: # Catch errors during the wait after terminate
                     logger.error(f"[PID:{pid}] Error waiting for process exit after SIGTERM: {e}", exc_info=True)

            except ProcessLookupError:
                 # This can happen if the process terminated between the is_running check and self.process.terminate()
                 logger.warning(f"[PID:{pid}] Process already terminated before SIGTERM could be sent.")
                 stopped_gracefully = True # Consider it stopped if lookup fails
            except Exception as e:
                logger.error(f"[PID:{pid}] Error during forceful termination (SIGTERM/SIGKILL): {e}", exc_info=True)

        # 3. Final check and cleanup
        # Check poll status directly after potential kill/terminate
        final_poll = self.process.poll()
        if final_poll is not None:
             logger.info(f"[PID:{pid}] Server stop sequence complete. Process confirmed stopped (exit code: {final_poll}).")
             self.process = None # Clear process handle
             return True
        else:
             # This case should be rare after SIGKILL
             logger.error(f"[PID:{pid}] Server stop sequence complete, but process poll() is still None!")
             return False

    async def _wait_for_process_exit(self, process: subprocess.Popen):
        """Helper async function to poll process exit without blocking the main thread."""
        while process.poll() is None:
            await asyncio.sleep(0.2) # Poll frequently
