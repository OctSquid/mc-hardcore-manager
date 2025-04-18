import discord
from discord import Interaction, TextChannel, Embed, utils as discord_utils
import logging
import asyncio
import shutil
import os
from typing import Optional, Callable, Awaitable

# Import new components and exceptions
from ..config import Config
from ..core.data_manager import DataManager, DataError
from .server_process_manager import ServerProcessManager, ServerProcessError
from ..core.exceptions import WorldManagementError

# Import views from their new location
# Note: ResetConfirmationView might need adjustments if it depends on old structures
# from ..discord_bot.views import ResetConfirmationView

logger = logging.getLogger(__name__)

# The request_reset_confirmation logic might be better placed within a Cog
# that handles the user interaction, rather than in the WorldManager itself.
# WorldManager should focus on the backend tasks.

# --- Helper function for logging progress ---
# This remains largely the same but uses logger directly

# --- Helper function for logging progress ---
async def _send_log(
    admin_channel: Optional[TextChannel],
    message: str,
    level: str = "info",
    embed: bool = True # Control if message should be embedded
):
    """Helper to send log messages to a Discord channel and the logger."""
    log_map = {
        "info": (logger.info, "ℹ️", discord.Color.blue()),
        "warning": (logger.warning, "⚠️", discord.Color.orange()),
        "error": (logger.error, "❌", discord.Color.red()),
        "success": (logger.info, "✅", discord.Color.green()),
        "critical": (logger.critical, "🔥", discord.Color.dark_red())
    }
    log_func, emoji, color = log_map.get(level.lower(), log_map["info"])

    log_func(message) # Log to console/file

    if admin_channel:
        try:
            if embed:
                log_embed = Embed(description=f"{emoji} {message}", color=color, timestamp=discord_utils.utcnow())
                await admin_channel.send(embed=log_embed)
            else:
                # Send plain text if needed
                await admin_channel.send(f"{emoji} {message}")
        except discord.Forbidden:
             logger.error(f"Missing permissions to send message in admin channel: {admin_channel.name}")
        except Exception as e:
            logger.error(f"Failed to send log to admin channel {admin_channel.name}: {e}")


class WorldManager:
    """Handles world-related operations like deletion and potentially backups."""

    def __init__(self, config: Config, data_manager: DataManager, server_process_manager: ServerProcessManager):
        self.config = config
        self.data_manager = data_manager
        self.server_process_manager = server_process_manager
        self.admin_channel: Optional[TextChannel] = None # Set externally or fetched

    def set_admin_channel(self, channel: TextChannel):
        """Sets the admin channel for progress updates."""
        self.admin_channel = channel

    async def _stop_server_step(self) -> bool:
        """Stops the server using ServerProcessManager."""
        await _send_log(self.admin_channel, "サーバーを停止しています...")
        try:
            stop_success = await self.server_process_manager.stop()
            if not stop_success:
                await _send_log(self.admin_channel, "サーバーの停止に失敗したか、確認できませんでした。処理を続行しますが、問題が発生する可能性があります。", "warning")
            else:
                await _send_log(self.admin_channel, "サーバーを停止しました。", "success")
                await asyncio.sleep(2) # Give time for full shutdown
            return stop_success
        except ServerProcessError as e:
             await _send_log(self.admin_channel, f"サーバー停止中にエラーが発生しました: {e}", "error")
             raise WorldManagementError("Failed to stop server during reset") from e
        except Exception as e:
             await _send_log(self.admin_channel, f"サーバー停止中に予期せぬエラーが発生しました: {e}", "critical")
             raise WorldManagementError("Unexpected error stopping server during reset") from e


    async def _delete_world_step(self):
        """Deletes the world folder specified in the config."""
        from pathlib import Path
        world_path_obj = self.config.server.world_path
        if isinstance(world_path_obj, str):
            world_path_obj = Path(world_path_obj)
        world_path = str(world_path_obj.resolve()) # Get absolute path string

        await _send_log(self.admin_channel, f"ワールドフォルダ (`{world_path}`) を削除しています...")

        # Validate path from config (Pydantic already checks if it's a directory)
        if not world_path_obj.exists():
             await _send_log(self.admin_channel, f"ワールドフォルダ (`{world_path}`) が見つかりませんでした。削除をスキップします。", "warning")
             return # Not an error if it doesn't exist

        # Add extra safety check - avoid deleting root or common system dirs
        # This is a basic check, might need refinement
        if world_path in ["/", "/usr", "/home", "/var", "/etc", os.path.expanduser("~")]:
             err_msg = f"エラー: 設定されたワールドパス '{world_path}' は危険な場所を指しているようです。安全のため削除を中止します。"
             await _send_log(self.admin_channel, err_msg, "critical")
             raise WorldManagementError(f"World path points to potentially dangerous location: {world_path}")

        try:
            # Run potentially long-running I/O in a thread to avoid blocking asyncio loop
            await asyncio.to_thread(shutil.rmtree, world_path)
            await _send_log(self.admin_channel, "ワールドフォルダを削除しました。", "success")
            await asyncio.sleep(1) # Brief pause for filesystem
        except Exception as e:
            err_msg = f"ワールドフォルダの削除中にエラーが発生しました: {e}"
            await _send_log(self.admin_channel, err_msg, "error")
            raise WorldManagementError(err_msg) from e

    async def _reset_stats_step(self):
        """Resets the statistics using DataManager."""
        data_path = self.config.data.path
        await _send_log(self.admin_channel, f"統計データ (`{data_path}`) をリセットしています...")
        try:
            # DataManager handles loading/saving internally now
            self.data_manager.reset_stats()
            await _send_log(self.admin_channel, "統計データをリセットしました。", "success")
        except DataError as e:
            err_msg = f"統計データのリセット中にエラーが発生しました: {e}"
            await _send_log(self.admin_channel, err_msg, "error")
            raise WorldManagementError(err_msg) from e
        except Exception as e:
             err_msg = f"統計データのリセット中に予期せぬエラーが発生しました: {e}"
             await _send_log(self.admin_channel, err_msg, "critical")
             raise WorldManagementError(err_msg) from e


    async def _restart_server_step(self) -> bool:
        """Restarts the server using ServerProcessManager."""
        await _send_log(self.admin_channel, "サーバーを再起動しています...")
        try:
            # ServerProcessManager.start returns (process, log_monitor) tuple
            process, _ = await self.server_process_manager.start()
            pid = process.pid
            await _send_log(self.admin_channel, f"サーバーが再起動しました (PID: {pid})。", "success")
            return True
        except ServerProcessError as e:
            await _send_log(self.admin_channel, f"サーバーの再起動に失敗しました: {e}", "error")
            return False
        except Exception as e:
             await _send_log(self.admin_channel, f"サーバー再起動中に予期せぬエラーが発生しました: {e}", "critical")
             return False


    async def execute_world_reset(self) -> bool:
        """
        Performs the full world reset sequence: stop, delete world, reset stats, restart.

        Returns:
            True if the reset completed successfully (including restart), False otherwise.
        Raises:
            WorldManagementError: If a critical step fails.
        """
        await _send_log(self.admin_channel, "**ワールドリセット処理を開始します...**", embed=False)
        reset_success = False
        try:
            # 1. Stop the server
            await self._stop_server_step()
            # If stop fails critically, it raises WorldManagementError

            # 2. Delete the world folder
            await self._delete_world_step()
            # If delete fails, it raises WorldManagementError

            # 3. Restart the server
            reset_success = await self._restart_server_step()
            # Restart failure is logged but doesn't raise, just returns False
            
            # 4. 新しいワールドの開始時間を更新
            if reset_success:
                # 新しいワールドの開始時間として現在時刻を設定
                self.data_manager._update_start_time()
                await _send_log(self.admin_channel, "**ワールドリセット処理が正常に完了しました。新しいワールドの開始時間を記録しました。**", "success", embed=False)
            else:
                await _send_log(self.admin_channel, "**ワールドリセット処理は完了しましたが、サーバーの再起動に失敗しました。**", "error", embed=False)

        except WorldManagementError as e:
            # Errors from steps are re-raised and caught here
            await _send_log(self.admin_channel, f"ワールドリセット処理中にエラーが発生し、処理が中断されました: {e}", "critical", embed=False)
            reset_success = False # Ensure failure is marked
        except Exception as e:
             # Catch any other unexpected errors
             logger.critical(f"Unexpected critical error during world reset: {e}", exc_info=True)
             await _send_log(self.admin_channel, f"ワールドリセット処理中に予期せぬ重大なエラーが発生しました: {e}", "critical", embed=False)
             reset_success = False

        return reset_success
