import discord
# from discord.ext import commands # Remove this line
from discord import commands # Add this line
import logging
import os
import asyncio
from typing import Optional, Set, List, Any

# Use absolute imports from the package root
from mc_hardcore_manager.config import Config, load_config
from mc_hardcore_manager.core.utils import setup_logging
from mc_hardcore_manager.core.data_manager import DataManager, DataError
# Import ConfigError from exceptions module
from mc_hardcore_manager.core.exceptions import McHardcoreManagerError, ConfigError, RconError, ServerProcessError, WorldManagementError, OpenAIError
from mc_hardcore_manager.minecraft.rcon_client import RconClient
from mc_hardcore_manager.minecraft.server_process_manager import ServerProcessManager
from mc_hardcore_manager.minecraft.world_manager import WorldManager
from mc_hardcore_manager.minecraft.log_monitor import LogMonitor
from mc_hardcore_manager.minecraft.death_event_dispatcher import DeathEventDispatcher
from mc_hardcore_manager.minecraft.scoreboard_manager import ScoreboardManager
from mc_hardcore_manager.death_handling.analyzer import DeathAnalyzer
from mc_hardcore_manager.death_handling.actions import DeathAction
from mc_hardcore_manager.death_handling.handler import DeathHandler

# カスタムBotクラスを定義して、追加の属性を型アノテーションで明示的に宣言
class MCHardcoreBot(discord.Bot): # Changed back to discord.Bot
    """拡張Botクラス: ハードコア企画管理に必要な追加属性を定義"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config: Optional[Config] = None
        self.data_manager: Optional[DataManager] = None
        self.rcon_client: Optional[RconClient] = None
        self.world_manager: Optional[WorldManager] = None
        self.death_handler: Optional[DeathHandler] = None
        self.server_process_manager: Optional[ServerProcessManager] = None
        self.scoreboard_manager: Optional[ScoreboardManager] = None

logger = logging.getLogger(__name__) # Get logger for this module

# --- Main Bot Logic ---
async def main():
    # 1. Setup Logging early
    # Consider making log level configurable via env var or config later
    setup_logging(level=logging.INFO)

    # 2. Load Configuration
    try:
        config = load_config() # Uses default path "config.yaml"
        logger.info("Configuration loaded successfully.")
    except (ConfigError, FileNotFoundError, ValueError) as e:
        logger.critical(f"Failed to load configuration: {e}. Exiting.")
        exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred during config loading: {e}", exc_info=True)
        exit(1)

    # 3. Initialize Discord Bot
    intents = discord.Intents.default()
    # Add necessary intents based on your bot's needs (e.g., reading messages, member info)
    # intents.message_content = True # If reading message content
    # intents.members = True      # If needing member information beyond cache

    bot = MCHardcoreBot(
        intents=intents,
        owner_ids=set(config.discord.owner_ids) # Use validated config
    )

    # 4. Initialize Core Components (Dependency Injection Setup)
    try:
        # イベントループの取得
        loop = asyncio.get_event_loop()
        
        data_manager = DataManager(str(config.data.path))
        rcon_client = RconClient(config.server.ip, config.rcon.port, config.rcon.password)
        # Pass config, rcon_client, and data_manager to ServerProcessManager
        server_process_manager = ServerProcessManager(config, rcon_client, data_manager)
        # Pass the already initialized server_process_manager to WorldManager
        world_manager = WorldManager(config, data_manager, server_process_manager) # Pass dependencies (Corrected WorldManager init too)
        death_analyzer = DeathAnalyzer(config.openai.api_key, str(config.openai.url), config.openai.model)
        death_action = DeathAction(rcon_client, config)
        
        # スコアボードマネージャーの作成
        scoreboard_manager = ScoreboardManager(rcon_client, config)
        logger.info("Scoreboard manager initialized")
        
        # DeathEventDispatcherの作成
        death_event_dispatcher = DeathEventDispatcher(loop)
        logger.info("Death event dispatcher initialized")
        
        # DeathHandler brings many components together
        death_handler = DeathHandler(
            bot=bot,
            config=config, # Pass config to DeathHandler
            data_manager=data_manager,
            rcon_client=rcon_client,
            world_manager=world_manager,
            death_analyzer=death_analyzer,
            death_action=death_action,
            death_event_dispatcher=death_event_dispatcher # Pass event dispatcher
        )
        # LogMonitor initialization needs the process object, which isn't available yet.
        # LogMonitor should likely be created and started within ServerCog when the server starts.
        # Remove LogMonitor initialization from here.
        # log_monitor = LogMonitor(config.server.script) # Remove this line

        # Attach shared components to the bot instance for easy access in Cogs
        # This is a simple form of dependency injection for discord.py cogs

        # Attach shared components to the bot instance for easy access in Cogs
        # This is a simple form of dependency injection for discord.py cogs
        bot.config = config
        bot.data_manager = data_manager
        # Pass bot instance to RconClient for direct death handling access
        rcon_client.bot = bot
        bot.rcon_client = rcon_client  
        bot.world_manager = world_manager
        bot.death_handler = death_handler
        bot.server_process_manager = server_process_manager
        bot.scoreboard_manager = scoreboard_manager
        # bot.log_monitor = log_monitor # Remove log_monitor attachment here

        logger.info("Core components initialized.")

    except DataError as e:
        logger.critical(f"Failed to initialize DataManager: {e}. Exiting.")
        exit(1)
    except RconError as e:
        logger.critical(f"Failed to initialize RconClient: {e}. Exiting.")
        # Maybe allow running without RCON? Depends on features.
        exit(1)
    except WorldManagementError as e:
         logger.critical(f"Failed to initialize WorldManager: {e}. Exiting.")
         exit(1)
    except OpenAIError as e:
         logger.critical(f"Failed to initialize DeathAnalyzer (OpenAI): {e}. Death cause analysis will fail.")
         # Decide if this is critical or if the bot can run without it
         # exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred during component initialization: {e}", exc_info=True)
        exit(1)


    # 5. Define Cogs to Load (using absolute paths from package root)
    cogs_to_load = [
        "mc_hardcore_manager.discord_bot.cogs.stats_cog",
        "mc_hardcore_manager.discord_bot.cogs.server_cog",
    ]

    # 6. Load Cogs
    logger.info("Loading cogs...")
    for cog_path in cogs_to_load:
        try:
            bot.load_extension(cog_path) # Removed await
            logger.info(f"Successfully loaded cog: {cog_path}")
        except discord.ExtensionNotFound: # Changed from discord.errors.ExtensionNotFound
            logger.error(f"Cog not found: {cog_path}", exc_info=True)
        except discord.ExtensionAlreadyLoaded: # Changed from discord.errors.ExtensionAlreadyLoaded
            logger.warning(f"Cog already loaded: {cog_path}")
        except discord.NoEntryPointError: # Changed from discord.errors.NoEntryPointError
             logger.error(f"Cog {cog_path} has no setup() function.", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to load cog {cog_path}: {e}", exc_info=True)
            # Decide if loading failure of one cog is critical
            # exit(1)
    logger.info("Cog loading complete.")


    # 7. Setup Bot Events (Simplified on_ready)
    @bot.event
    async def on_ready():
        # userがNoneでないことを確認
        if bot.user is not None:
            logger.info(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
        else:
            logger.info('Logged in (user information not available)')
        logger.info('------')
        logger.info('Bot is ready and online.')
        
        try:
            # Initialize DeathHandler channels
            if hasattr(bot, 'death_handler') and bot.death_handler is not None:
                if hasattr(bot.death_handler, 'initialize_channels') and callable(bot.death_handler.initialize_channels):
                    await bot.death_handler.initialize_channels()
                    logger.info("DeathHandler channels initialized on bot ready")
                else:
                    logger.warning("DeathHandler does not have initialize_channels method")
            else:
                logger.warning("DeathHandler not found on bot instance during on_ready")
                
            # If server is already running, check LogMonitor configuration
            # ServerCog.start_server will handle this when starting the server
            # Just perform initial state check here
            server_cog = bot.get_cog("ServerCog")  # このget_cogの戻り値は非同期ではないので問題なし
            if server_cog is not None:
                # ServerCogの場合、botのserver_process_managerを使用してサーバーの状態を確認
                if bot.server_process_manager is not None and hasattr(bot.server_process_manager, 'is_running'):
                    # is_running()がコルーチン関数かどうかチェック
                    if asyncio.iscoroutinefunction(bot.server_process_manager.is_running):
                        is_running = await bot.server_process_manager.is_running()
                        if is_running:
                            logger.info("Server is already running on bot startup - LogMonitor should be configured in ServerCog")
                    elif bot.server_process_manager.is_running():
                        logger.info("Server is already running on bot startup - LogMonitor should be configured in ServerCog")
                        # すでに実行中のサーバーに対してLogMonitor設定を行う場合はここで明示的に設定する
            
        except Exception as e:
            logger.error(f"Failed to start background tasks: {e}", exc_info=True)


    # 8. Run the Bot
    bot_token = config.discord.token
    if not bot_token or bot_token == "YOUR_DISCORD_BOT_TOKEN": # Check against placeholder
         logger.critical("Discord bot token is missing or not set in config.yaml. Please update it.")
         print("\nError: Discord bot token is missing or not set in config.yaml.")
         print("Please update the 'token' field under the 'discord' section and restart the bot.")
         exit(1)

    try:
        logger.info("Starting bot...")
        await bot.start(bot_token)
    except discord.LoginFailure: # Keep as discord.LoginFailure (already top-level)
        logger.critical("Invalid Discord token provided. Please check config.yaml.")
    except Exception as e:
        logger.critical(f"An error occurred while running the bot: {e}", exc_info=True)
    finally:
        logger.info("Bot is shutting down...")
        # Add cleanup tasks if needed (e.g., close RCON connection, stop server process)
        if hasattr(bot, 'rcon_client') and bot.rcon_client is not None:
            if hasattr(bot.rcon_client, 'is_connected') and callable(bot.rcon_client.is_connected):
                try:
                    if asyncio.iscoroutinefunction(bot.rcon_client.is_connected):
                        # Need to create a task since we can't await in finally
                        is_connected = await bot.rcon_client.is_connected()
                        if is_connected:
                            try:
                                await bot.rcon_client.close()
                                logger.info("RCON client closed.")
                            except Exception as close_err:
                                logger.error(f"Error during async RCON close: {close_err}")
                    elif bot.rcon_client.is_connected():
                        try:
                            # close()メソッドも非同期関数かどうかチェック
                            if asyncio.iscoroutinefunction(bot.rcon_client.close):
                                close_result = await bot.rcon_client.close()
                                logger.info("RCON client closed (async).")
                            else:
                                result = bot.rcon_client.close()
                                logger.info("RCON client closed (sync).")
                        except Exception as sync_close_err:
                            logger.error(f"Error during sync RCON close: {sync_close_err}")
                except Exception as e_connect:
                    logger.error(f"Error checking RCON connection: {e_connect}")

        if hasattr(bot, 'server_process_manager') and bot.server_process_manager is not None:
            if hasattr(bot.server_process_manager, 'is_running') and callable(bot.server_process_manager.is_running):
                try:
                    # is_running()も非同期関数の可能性を考慮
                    if asyncio.iscoroutinefunction(bot.server_process_manager.is_running):
                        is_running = await bot.server_process_manager.is_running()
                        if is_running:
                            try:
                                stop_result = await bot.server_process_manager.stop() # Use existing stop method
                                logger.info(f"Server process stopped: {stop_result}")
                            except Exception as stop_err:
                                logger.error(f"Error during server stop: {stop_err}")
                    elif bot.server_process_manager.is_running():
                        try:
                            stop_result = await bot.server_process_manager.stop() # Use existing stop method
                            logger.info(f"Server process stopped: {stop_result}")
                        except Exception as stop_err:
                            logger.error(f"Error during server stop: {stop_err}")
                except Exception as e_running:
                    logger.error(f"Error checking server running state: {e_running}")
                    
        # Ensure asyncio tasks are cancelled
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        # Cancel each task individually
        for task in tasks:
            task.cancel()
        # Wait for all tasks to complete with cancellation
        if tasks:  # Only gather if there are tasks
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                # 結果のログ出力（必要に応じて）
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.debug(f"Task {i} cancelled with exception: {result}")
            except Exception as gather_err:
                logger.error(f"Error during task cancellation: {gather_err}")
        logger.info("Remaining asyncio tasks cancelled.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Critical error in main execution loop: {e}", exc_info=True)
