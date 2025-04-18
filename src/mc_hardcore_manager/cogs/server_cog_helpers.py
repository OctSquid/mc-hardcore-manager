import discord
from discord.ext import commands
from discord.commands import ApplicationContext
import logging
import asyncio
from typing import TYPE_CHECKING

from mc_hardcore_manager.minecraft.log_monitor import LogMonitor

# Avoid circular imports for type checking
if TYPE_CHECKING:
    from mc_hardcore_manager.discord_bot.cogs.server_cog import ServerCog

logger = logging.getLogger(__name__)

# --- Internal Start/Stop Helpers ---

async def internal_start_server(cog: ServerCog) -> bool:
    """Starts the server process using the manager and initializes log monitoring."""
    logger.info("Internal start server requested.")
    # Start the process using the manager
    process = cog.process_manager.start() # This is synchronous but quick

    # Allow a brief moment for the process to potentially fail immediately
    await asyncio.sleep(1.5)

    if process and cog.process_manager.is_running():
        logger.info(f"Server process started successfully (PID: {cog.process_manager.get_pid()}). Initializing log monitor.")
        # Stop existing monitor if any (shouldn't happen ideally)
        if cog.log_monitor:
            logger.warning("Existing log monitor found during start, stopping it.")
            cog.log_monitor.stop()

        # Start new log monitor
        try:
             cog.log_monitor = LogMonitor(process, cog.bot.loop, cog.death_handler_callback)
             cog.log_monitor.start()
             logger.info("Log monitor started.")
             return True
        except Exception as e:
             logger.error(f"Failed to start log monitor after server start: {e}", exc_info=True)
             # Attempt to stop the started process if monitor fails
             await cog.process_manager.stop()
             return False
    else:
        logger.error("Server process failed to start or terminated immediately.")
        cog.log_monitor = None # Ensure monitor is None
        return False

async def internal_stop_server(cog: 'ServerCog') -> bool:
    """
    Internal logic to stop the Minecraft server process.
    Stops the log monitor.
    Uses ServerProcessManager for the actual process stopping.
    Returns True if the process is confirmed stopped, False otherwise.
    """
    if not cog.process_manager.is_running():
        logger.info("Internal stop called but server not running.")
        if cog.log_monitor: # Stop monitor even if process was already gone
             cog.log_monitor.stop()
             cog.log_monitor = None
        return True # Already stopped

    pid = cog.process_manager.get_pid()
    logger.warning(f"Attempting internal stop for Minecraft server (PID: {pid})")

    # 1. Stop Log Monitor first to prevent race conditions with process exit
    if cog.log_monitor:
        logger.info(f"[PID:{pid}] Stopping log monitor...")
        cog.log_monitor.stop()
        cog.log_monitor = None # Clear monitor instance
        logger.info(f"[PID:{pid}] Log monitor stopped.")
    else:
         logger.warning(f"[PID:{pid}] Log monitor instance not found during stop.")

    # 2. Stop the process using the manager
    stop_success = await cog.process_manager.stop()
    return stop_success

# --- RCON Status Helper ---

async def get_rcon_status_details(cog: 'ServerCog') -> str:
    """Connects via RCON to get player list and status."""
    if await cog.rcon_client.connect():
        player_list_str = "プレイヤーリストの取得に失敗しました。"
        try:
            response = await cog.rcon_client.command("list")
            if response:
                 parts = response.split(":")
                 if len(parts) > 0:
                     player_info = parts[0].strip() # "There are X of a max Y players online"
                     player_names = parts[1].strip() if len(parts) > 1 else "なし"
                     player_list_str = f"{player_info}\nプレイヤー: {player_names}"
                 else:
                     player_list_str = response # Fallback to raw response
            else:
                 player_list_str = "サーバーから応答がありません (listコマンド)。"
        except Exception as e:
            logger.warning(f"Error getting player list via RCON: {e}")
            player_list_str = f"RCON通信エラー: {e}"
        finally:
            await cog.rcon_client.disconnect()
        return f"RCON接続: 可能\n{player_list_str}"
    else:
         return "RCON接続: 失敗 (サーバーが起動直後か、設定が間違っている可能性があります)"

# --- Error Handlers ---

async def handle_server_command_error(cog: 'ServerCog', ctx: ApplicationContext, error):
    """Error handler for server start/stop commands."""
    if isinstance(error, commands.NotOwner):
        await ctx.respond("このコマンドはBotのオーナーのみが実行できます。", ephemeral=True)
        logger.warning(f"Unauthorized server command attempt by {ctx.author.name}")
    else:
        # Check if response already sent before sending another
        try:
             if ctx.interaction.response.is_done():
                  await ctx.followup.send(f"サーバーコマンドの実行中にエラーが発生しました: {error}", ephemeral=True)
             else:
                  await ctx.respond(f"サーバーコマンドの実行中にエラーが発生しました: {error}", ephemeral=True)
        except Exception as e:
             logger.error(f"Error sending command error message: {e}")
        logger.error(f"Error in server command '{ctx.command.name}': {error}", exc_info=True)

async def handle_reset_world_error(cog: 'ServerCog', ctx: ApplicationContext, error):
    """Error handler specifically for the resetworld command."""
    if isinstance(error, commands.NotOwner):
        await ctx.respond("このコマンドはBotのオーナーのみが実行できます。", ephemeral=True)
        logger.warning(f"Unauthorized resetworld command attempt by {ctx.author.name}")
    else:
         logger.error(f"Error in resetworld command: {error}", exc_info=True)
         try:
             if ctx.interaction.response.is_done():
                  await ctx.followup.send(f"ワールドリセットコマンドの実行中にエラーが発生しました: {error}", ephemeral=True)
             else:
                  await ctx.respond(f"ワールドリセットコマンドの実行中にエラーが発生しました: {error}", ephemeral=True)
         except Exception as e:
              logger.error(f"Error sending reset_world error message: {e}")
