import discord
from discord.ext import commands
from discord import slash_command, ApplicationContext, Interaction, TextChannel, Embed
import logging
import asyncio
from typing import Optional, TYPE_CHECKING, cast
if TYPE_CHECKING:
    from ...main import MCHardcoreBot

from ...config import Config
from ...minecraft.rcon_client import RconClient, RconError
from ...minecraft.log_monitor import LogMonitor
from ...minecraft.server_process_manager import ServerProcessManager, ServerProcessError
from ...minecraft.world_manager import WorldManager, WorldManagementError

logger = logging.getLogger(__name__)

class ServerCog(commands.Cog):
    """Cog for managing the Minecraft server process, status, and world resets."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Get dependencies from bot instance
        self.config: Config = getattr(bot, 'config')
        self.server_process_manager: ServerProcessManager = getattr(bot, 'server_process_manager')
        self.world_manager: WorldManager = getattr(bot, 'world_manager')
        self.rcon_client: RconClient = getattr(bot, 'rcon_client')
        # LogMonitor might be created per server start, or managed centrally
        self.log_monitor: Optional[LogMonitor] = None # Initialize as None
        self._rcon_monitor_task = None  # RCONモニタリングタスクの参照

        if not all([self.config, self.server_process_manager, self.world_manager, self.rcon_client]):
             logger.critical("ServerCog failed to initialize dependencies from bot instance!")
             raise RuntimeError("ServerCog missing dependencies")

        # DeathEventDispatcherとDeathHandlerを取得
        death_handler = getattr(bot, 'death_handler', None)
        if death_handler:
            # ServerCogのメソッドをディスパッチャーに登録
            if hasattr(death_handler, 'death_event_dispatcher'):
                logger.info("Registering ServerCog methods with death_event_dispatcher")
                # ここでイベントハンドラを登録するメソッドがあれば登録
                # 例: self.on_player_death など
                # bot.death_handler.death_event_dispatcher.register_death_handler(self.on_player_death)
                # なければコメントアウトしてください
        else:
            logger.warning("DeathHandler not available to register ServerCog handlers")

        logger.info("ServerCog initialized.")

    # Removed set_death_handler - LogMonitor uses bot.dispatch now
    # Removed _internal_start/stop_server - logic moved to commands

    # --- Slash Commands ---

    # --- Slash Commands ---

    @slash_command(name="startserver", description="Minecraftサーバーを起動します。(オーナー限定)")
    @commands.is_owner()
    async def start_server(self, ctx: ApplicationContext):
        """Starts the Minecraft server using ServerProcessManager."""
        await ctx.defer(ephemeral=True) # Acknowledge command quickly

        if self.server_process_manager.is_running():
            await ctx.followup.send("サーバーは既に実行中です。", ephemeral=True)
            return

        try:
            logger.info(f"Server start requested by {ctx.author.name}")
            process, log_monitor = await self.server_process_manager.start() # Use async start
            pid = process.pid
            await ctx.followup.send(f"✅ サーバーが起動しました (PID: {pid})。")

            # Update log monitor reference
            if self.log_monitor and self.log_monitor._threads_started:
                logger.info("Stopping previous log monitor...")
                self.log_monitor.stop()
            
            # Store the monitor reference - the death handler is already configured in ServerProcessManager
            self.log_monitor = log_monitor
            
            logger.info(f"Log monitoring started for new server process (PID: {pid})")
            
            # RCON接続監視とスコアボード更新タスクを開始
            monitoring_task = asyncio.create_task(self._monitor_rcon_and_update_scoreboard(is_after_reset=False))
            # エラー処理のためにタスク参照を保持
            self._rcon_monitor_task = monitoring_task

        except ServerProcessError as e:
            logger.error(f"Failed to start server: {e}", exc_info=True)
            await ctx.followup.send(f"❌ サーバーの起動に失敗しました: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during server start command: {e}", exc_info=True)
            await ctx.followup.send(f"❌ サーバーの起動中に予期せぬエラーが発生しました: {e}")
            
    async def _monitor_rcon_and_update_scoreboard(self, is_after_reset: bool = False):
        """
        サーバー起動後、一定間隔でRCON接続を試み、接続が確立したらスコアボードを更新する。
        接続と切断を最小限に抑えるため、更新処理中はRCON接続を維持する。
        """
        context = "world reset" if is_after_reset else "server start"
        logger.info(f"Starting RCON connection monitoring after {context}...")
        
        # 監視の設定
        max_attempts = 20  # 最大試行回数
        attempt = 0
        check_interval = 3  # 秒
        
        while attempt < max_attempts and self.server_process_manager.is_running():
            attempt += 1
            
            # RCON接続テスト
            connection_successful = False
            try:
                # 接続状態をチェックする前に、現在の接続状態を確認
                is_connected = await self.rcon_client.is_connected()
                if is_connected:
                    # 既に接続している場合は接続状態のみテスト
                    connection_successful = await self.rcon_client.test_connection()
                    if not connection_successful:
                        # 接続が失われていたら切断
                        await self.rcon_client.disconnect()
                else:
                    # RCONサーバーへの接続を試みる
                    await self.rcon_client.connect()
                    connection_successful = True
            except Exception as e:
                logger.debug(f"RCON connection attempt {attempt}/{max_attempts} failed: {e}")
                connection_successful = False
            
            # 接続に成功したらスコアボードを更新
            if connection_successful:
                logger.info(f"RCON connection established on attempt {attempt}, updating scoreboard...")
                
                try:
                    # スコアボードを更新
                    scoreboard_manager = getattr(self.bot, 'scoreboard_manager', None)
                    data_manager = getattr(self.bot, 'data_manager', None)
                    if scoreboard_manager and data_manager:
                        # 接続を管理しない（このメソッド内で既に接続している）
                        await scoreboard_manager.init_death_count_scoreboard(manage_connection=False)
                        # プレイヤーの死亡回数をスコアボードに反映（接続を管理しない）
                        await scoreboard_manager.update_player_death_counts(data_manager, manage_connection=False)
                        logger.info(f"Scoreboard updated successfully after RCON connection (attempt {attempt})")
                        
                        # スコアボード更新が成功したら、接続を切断して終了
                        await self.rcon_client.disconnect()
                        break
                    else:
                        logger.warning("Scoreboard/data manager not found, scoreboard not updated")
                        await self.rcon_client.disconnect()
                except Exception as e:
                    logger.error(f"Error updating scoreboard after RCON connection: {e}", exc_info=True)
                    # エラーが発生した場合も接続を閉じる
                    if await self.rcon_client.is_connected():
                        await self.rcon_client.disconnect()
            
            # 次の試行まで待機
            if attempt < max_attempts:
                await asyncio.sleep(check_interval)
        
        # 最後に接続状態を確認して、接続が残っていたら切断
        if await self.rcon_client.is_connected():
            await self.rcon_client.disconnect()
            
        if attempt >= max_attempts:
            logger.info(f"RCON monitoring completed after {max_attempts} attempts")
        else:
            logger.info(f"RCON monitoring completed successfully after {attempt} attempts")
            
    async def _initialize_scoreboard_when_ready(self, is_after_reset: bool = False):
        """サーバーがRCON接続を受け付けるようになったらスコアボードを初期化する"""
        context = "world reset" if is_after_reset else "server start"
        logger.info(f"Waiting for RCON to become available before initializing scoreboard after {context}...")
        
        # サーバーがRCON接続を受け付けるようになるまで試行する
        max_attempts = 10
        attempt = 0
        retry_delay = 2  # 秒
        
        rcon_ready = False
        
        while attempt < max_attempts and not rcon_ready:
            attempt += 1
            try:
                # RCON接続テスト
                is_connected = await self.rcon_client.is_connected()
                if is_connected:
                    # すでに接続している場合は接続テスト
                    if await self.rcon_client.test_connection():
                        rcon_ready = True
                    else:
                        # 接続が失われていたら切断
                        await self.rcon_client.disconnect()
                else:
                    # 新規接続してテスト
                    await self.rcon_client.connect()
                    # 基本的なコマンドを試行
                    await self.rcon_client.command("list")
                    rcon_ready = True
                
                if rcon_ready:
                    logger.info(f"RCON connection successful after {attempt} attempt(s)")
            except Exception as e:
                logger.debug(f"RCON not ready on attempt {attempt}/{max_attempts}: {e}")
                # エラーが発生した場合、接続が残っていたら切断
                try:
                    if await self.rcon_client.is_connected():
                        await self.rcon_client.disconnect()
                except:
                    pass
                    
                if attempt < max_attempts:
                    await asyncio.sleep(retry_delay)
        
        if not rcon_ready:
            logger.error(f"Failed to connect to RCON after {max_attempts} attempts, scoreboard initialization skipped")
            return
            
        # スコアボードを初期化 - 接続は既に確立済み
        try:
            scoreboard_manager = getattr(self.bot, 'scoreboard_manager', None)
            data_manager = getattr(self.bot, 'data_manager', None)
            if scoreboard_manager and data_manager:
                logger.info(f"Initializing scoreboard after {context}")
                # 接続を管理しない（すでに接続済み）
                await scoreboard_manager.init_death_count_scoreboard(manage_connection=False)
                # プレイヤーの死亡回数をスコアボードに反映（接続を管理しない）
                await scoreboard_manager.update_player_death_counts(data_manager, manage_connection=False)
                logger.info("Scoreboard initialized after RCON became ready")
            else:
                logger.warning("Scoreboard manager not found, scoreboard not initialized")
        except Exception as e:
            logger.error(f"Error initializing scoreboard: {e}", exc_info=True)
        finally:
            # 処理が完了したら接続を閉じる
            if await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()

    @slash_command(name="stopserver", description="Minecraftサーバーを停止します。(オーナー限定)")
    @commands.is_owner()
    async def stop_server(self, ctx: ApplicationContext):
        """Stops the Minecraft server using ServerProcessManager."""
        if not self.server_process_manager.is_running():
            await ctx.respond("サーバーは実行されていません。", ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        logger.info(f"Server stop requested by {ctx.author.name}")

        # Stop log monitoring and RCON monitoring task first
        if self.log_monitor:
            logger.info("Stopping log monitor before stopping server...")
            self.log_monitor.stop()
            self.log_monitor = None # Clear instance
            
        # Cancel RCON monitoring task if running
        if self._rcon_monitor_task and not self._rcon_monitor_task.done():
            logger.info("Cancelling RCON monitoring task...")
            self._rcon_monitor_task.cancel()
            self._rcon_monitor_task = None

        try:
            success = await self.server_process_manager.stop() # Use async stop
            if success:
                await ctx.followup.send("✅ サーバー停止処理を実行し、停止を確認しました。")
            else:
                 # process_manager.stop logs errors internally
                 await ctx.followup.send("⚠️ サーバー停止処理を実行しましたが、プロセスが終了しない可能性があります。手動での確認/停止が必要かもしれません。")
        except ServerProcessError as e:
             logger.error(f"ServerProcessError during server stop: {e}", exc_info=True)
             await ctx.followup.send(f"❌ サーバー停止処理中にエラーが発生しました: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during server stop command: {e}", exc_info=True)
            await ctx.followup.send(f"❌ サーバー停止処理中に予期せぬエラーが発生しました: {e}")


    @slash_command(name="serverstatus", description="Minecraftサーバーの現在の状態を表示します。")
    async def server_status(self, ctx: ApplicationContext):
        """Checks and reports the status of the Minecraft server process and RCON."""
        if self.server_process_manager.is_running():
            pid = self.server_process_manager.get_pid()
            status_embed = Embed(title="サーバー状態", color=discord.Color.green())
            status_embed.add_field(name="プロセス状態", value=f"🟢 実行中 (PID: {pid})", inline=False)

            # Check RCON status - test_connectionメソッドを使用
            rcon_status = "🔴 不明/接続不可"
            try:
                if await self.rcon_client.is_connected():
                    # 既に接続している場合はテストのみ
                    if await self.rcon_client.test_connection():
                        rcon_status = "🟢 接続可能"
                else:
                    # 新規接続してテスト
                    await self.rcon_client.connect()
                    # テスト成功が確認できれば接続可能
                    rcon_status = "🟢 接続可能"
                    # テスト後に切断
                    await self.rcon_client.disconnect()
            except RconError as e:
                logger.warning(f"RCON status check failed: {e}")
                # Keep status as "接続不可"
            except Exception as e:
                logger.error(f"Unexpected error during RCON status check: {e}", exc_info=True)
                rcon_status = "⚠️ チェック中にエラー"

            status_embed.add_field(name="RCON状態", value=rcon_status, inline=False)
            await ctx.respond(embed=status_embed)
        else:
            status_embed = Embed(title="サーバー状態", color=discord.Color.red())
            status_embed.add_field(name="プロセス状態", value="🔴 停止中", inline=False)
            status_embed.add_field(name="RCON状態", value="🔴 接続不可", inline=False)
            await ctx.respond(embed=status_embed)


    @slash_command(name="resetworld", description="ワールドと統計をリセットし、サーバーを再起動します。(オーナー限定)")
    @commands.is_owner()
    async def reset_world(self, ctx: ApplicationContext):
        """Stops the server, deletes world/stats via WorldManager, and restarts."""

        # --- Confirmation View ---
        class WorldResetConfirmationView(discord.ui.View):
            def __init__(self, world_manager_instance: WorldManager):
                super().__init__(timeout=60.0) # Longer timeout for reset confirmation
                self.world_manager = world_manager_instance
                self.confirmed: Optional[bool] = None
                self.interaction_message: Optional[discord.Message] = None

            async def interaction_check(self, interaction: Interaction) -> bool:
                 # interaction.userとctx.authorがどちらもNoneでないことを確認
                 if interaction.user is None or ctx.author is None:
                     logger.warning("Interaction user or ctx.author is None in interaction check")
                     return False
                     
                 is_author = interaction.user.id == ctx.author.id
                 if not is_author:
                      await interaction.response.send_message("この操作はコマンドを実行した本人のみが行えます。", ephemeral=True)
                 return is_author

            @discord.ui.button(label="はい、リセット実行", style=discord.ButtonStyle.danger, custom_id="confirm_world_reset")
            async def confirm_button(self, button: discord.ui.Button, interaction: Interaction):
                self.confirmed = True
                self.stop()
                # childrenの各アイテムを型情報付きで処理
                from discord.ui import Item
                for child in self.children:
                    if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                        # disabledプロパティにアクセスする前に型をチェック
                        # pyright: ignore[reportAttributeAccessIssue]
                        child.disabled = True  # type: ignore
                await interaction.response.edit_message(content="ワールドリセット処理を開始します...", view=self)

            @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="cancel_world_reset")
            async def cancel_button(self, button: discord.ui.Button, interaction: Interaction):
                self.confirmed = False
                self.stop()
                # childrenの各アイテムを型情報付きで処理
                from discord.ui import Item
                for child in self.children:
                    if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                        # pyright: ignore[reportAttributeAccessIssue]
                        child.disabled = True  # type: ignore
                await interaction.response.edit_message(content="ワールドリセットはキャンセルされました。", view=self)

            async def on_timeout(self):
                self.confirmed = None
                # childrenの各アイテムを型情報付きで処理
                from discord.ui import Item
                for child in self.children:
                    if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                        # pyright: ignore[reportAttributeAccessIssue]
                        child.disabled = True  # type: ignore
                if self.interaction_message:
                    try:
                        await self.interaction_message.edit(content="ワールドリセット確認がタイムアウトしました。", view=self)
                    except discord.NotFound: logger.warning("Original reset confirmation message not found on timeout.")
                    except Exception as e: logger.error(f"Error editing message on timeout: {e}")

        # --- Command Logic ---
        view = WorldResetConfirmationView(self.world_manager)
        await ctx.respond(
            "⚠️ **警告:** 本当にワールドと統計をリセットしますか？\n"
            "サーバー停止 → ワールド削除 → 統計リセット → サーバー再起動 が実行されます。\n"
            "**この操作は元に戻せません！**",
            view=view,
            ephemeral=True
        )
        view.interaction_message = await ctx.interaction.original_response()
        await view.wait()

        if view.confirmed is not True:
            if view.confirmed is False: # Explicit cancel
                 await ctx.followup.send("ワールドリセットはキャンセルされました。", ephemeral=True)
            else: # Timeout
                 await ctx.followup.send("ワールドリセット確認がタイムアウトしました。", ephemeral=True)
            logger.info(f"World reset cancelled or timed out (requested by {ctx.author.name}).")
            return

        # --- Proceed with Reset ---
        logger.warning(f"World reset initiated by {ctx.author.name}.")
        await ctx.followup.send("ワールドリセット処理を開始します。進捗はAdminチャンネルに通知されます。", ephemeral=True)

        # Ensure admin channel is set in WorldManager for logging
        admin_channel = None
        try:
            admin_channel_id = self.config.discord.admin_channel_id
            admin_channel = await self.bot.fetch_channel(admin_channel_id)
            if isinstance(admin_channel, TextChannel):
                 self.world_manager.set_admin_channel(admin_channel)
            else:
                 logger.error(f"Configured admin channel {admin_channel_id} is not a TextChannel.")
                 admin_channel = None # Fallback handled below
        except (discord.NotFound, discord.Forbidden) as e:
             logger.error(f"Could not fetch admin channel {admin_channel_id}: {e}")
             admin_channel = None

        if not admin_channel:
             # Try using the command's channel as a fallback if it's a TextChannel
             if isinstance(ctx.channel, TextChannel):
                  admin_channel = ctx.channel
                  self.world_manager.set_admin_channel(admin_channel)
                  logger.warning(f"Admin channel not found/invalid, using current channel {ctx.channel.id} for reset logs.")
                  await ctx.followup.send("⚠️ Adminチャンネルが見つからないため、このチャンネルに進捗を通知します。", ephemeral=True)
             else:
                  logger.error("Admin channel not found and current channel is not TextChannel. Cannot report reset progress.")
                  await ctx.followup.send("❌ エラー: 進捗を報告するためのAdminチャンネルが見つかりません。リセット処理を中止します。", ephemeral=True)
                  return

        # Execute reset using WorldManager instance
        try:
            success = await self.world_manager.execute_world_reset()
            if success:
                # ワールドリセット後すぐにスコアボードを更新
                try:
                    scoreboard_manager = getattr(self.bot, 'scoreboard_manager', None)
                    data_manager = getattr(self.bot, 'data_manager', None)
                    if scoreboard_manager and data_manager:
                        logger.info("Initializing scoreboard immediately after world reset")
                        # プレイヤーの死亡回数をスコアボードに反映
                        await scoreboard_manager.update_player_death_counts(data_manager)
                        logger.info("Scoreboard initialized successfully after world reset")
                    else:
                        logger.warning("Scoreboard manager not found, scoreboard not initialized after world reset")
                except Exception as e:
                    logger.error(f"Error initializing scoreboard after world reset: {e}", exc_info=True)
                # Get new log monitor from server process manager
                if self.server_process_manager.is_running():
                    process = self.server_process_manager.process
                    if process:
                        from ...minecraft.log_monitor import LogMonitor
                        if self.log_monitor:
                            logger.info("Stopping previous log monitor...")
                            self.log_monitor.stop()
                            self.log_monitor = None
                        
                        logger.info("Creating new log monitor instance...")
                        # ServerProcessManagerと同様にDeathHandler.handle_deathを直接使用
                        # 型定義をインポート
                        from typing import cast, Optional
                        from ...minecraft.server_process_manager import DeathHandlerType

                        death_handler_fn: Optional[DeathHandlerType] = None
                        if hasattr(self.bot, 'death_handler') and getattr(self.bot, 'death_handler', None):
                            death_handler = getattr(self.bot, 'death_handler')
                            if hasattr(death_handler, 'handle_death') and callable(death_handler.handle_death):
                                # 明示的に型キャストする
                                death_handler_fn = cast(DeathHandlerType, death_handler.handle_death)
                                logger.info(f"Using DeathHandler.handle_death function directly for death events: {death_handler_fn}")
                            else:
                                logger.warning("DeathHandler found but handle_death method is missing or not callable")
                        else:
                            logger.warning("Bot death_handler not found or not set properly")
                        
                        self.log_monitor = LogMonitor(
                            process,
                            asyncio.get_running_loop(),
                            death_handler_fn
                        )
                        self.log_monitor.start()
                        logger.info("Log monitoring restarted after world reset with event dispatcher.")

                        # The death handler is already configured in the LogMonitor created by ServerProcessManager
                        logger.info("Log monitoring restarted with direct death handling after world reset")
                        
                        # ログ監視によるRCON準備完了検出を使用するようになったため、個別のタスク開始は不要
                        logger.info("RCON automatic connection monitor disabled after world reset - using log-based detection instead")
                
                await ctx.followup.send("✅ ワールドリセット処理が正常に完了しました。", ephemeral=True)
            else:
                await ctx.followup.send("❌ ワールドリセット処理中にエラーが発生しました。詳細はAdminチャンネルを確認してください。", ephemeral=True)
        except WorldManagementError as e:
             logger.critical(f"WorldManagementError during reset: {e}", exc_info=True)
             await ctx.followup.send(f"❌ ワールドリセット処理中に致命的なエラーが発生しました: {e}", ephemeral=True)
        except Exception as e:
            logger.critical(f"Unexpected critical error during reset command: {e}", exc_info=True)
            await ctx.followup.send(f"❌ ワールドリセット処理中に予期せぬ重大なエラーが発生しました: {e}", ephemeral=True)


    # --- Error Handlers ---
    async def cog_command_error(self, ctx: ApplicationContext, error: Exception):
        """Generic error handler for commands in this cog."""
        try:
            if isinstance(error, commands.NotOwner):
                msg = "このコマンドはBotのオーナーのみが実行できます。"
                logger.warning(f"Unauthorized command attempt by {ctx.author.name} ({ctx.author.id}) in ServerCog: {ctx.command.name}")
            elif isinstance(error, commands.CheckFailure):
                msg = "コマンドの実行権限がありません。"
                logger.warning(f"Check failure for {ctx.command.name} by {ctx.author.name} ({ctx.author.id}): {error}")
            elif isinstance(error, (ServerProcessError, WorldManagementError, RconError)):
                msg = f"サーバー操作中にエラーが発生しました: {error}"
                logger.error(f"Error executing {ctx.command.name} for {ctx.author.name}: {error}", exc_info=True)
            else:
                msg = "コマンドの実行中に予期せぬエラーが発生しました。詳細はログを確認してください。"
                logger.error(f"Unexpected error in command {ctx.command.name} for {ctx.author.name}: {error}", exc_info=True)

            # Check if interaction has already been responded to
            if not ctx.interaction.response.is_done():
                await ctx.respond(msg, ephemeral=True)
            else:
                try:
                    await ctx.followup.send(msg, ephemeral=True)
                except discord.NotFound:
                    logger.warning(f"Failed to send error message to {ctx.author.name}: interaction not found")
                except discord.HTTPException as e:
                    logger.error(f"Failed to send error message to {ctx.author.name}: {e}")

        except Exception as e:
            logger.critical(f"Error in cog_command_error handler: {e}", exc_info=True)


    # --- Cog Unload ---
    def cog_unload(self):
        """Cleans up resources when the cog is unloaded."""
        logger.info("Unloading ServerCog...")
        # Stop log monitoring if it's running
        if self.log_monitor:
            self.log_monitor.stop()
            self.log_monitor = None
        # Attempt to stop the server process if it's running
        if self.server_process_manager.is_running():
            logger.warning("Server process still running during ServerCog unload. Attempting async stop...")
            # Running async stop in unload is tricky. Best effort: create task.
            try:
                # Ensure loop is running or get reference if needed
                loop = asyncio.get_event_loop()
                # Create task without awaiting it
                # We can't await the task here because this is a synchronous method
                # We also intentionally ignore the task object (not storing it in a variable)
                # because we don't need to track its status in this cleanup context
                # pylint: disable=unused-result
                # 明示的にフラグを立ててこのコード行を無視する
                # ignore coroutine not awaited warnings
                # we can't use await inside this sync method
                stop_task = loop.create_task(self.server_process_manager.stop())  # type: ignore
                # タスクを無視しているという意図を明示
                _ = stop_task  # pyright: ignore[reportUnusedVariable]
            except RuntimeError: # If loop isn't running
                 logger.error("Cannot schedule async server stop during unload: no running event loop.")
            except Exception as e:
                 logger.error(f"Error scheduling async server stop during unload: {e}")

        # Disconnect RCON client
        if hasattr(self.rcon_client, 'is_connected') and callable(self.rcon_client.is_connected):
            try:
                # Check if is_connected is an async method
                if asyncio.iscoroutinefunction(self.rcon_client.is_connected):
                    # Cannot await in this synchronous method, so just log it
                    logger.info("Skipping async RCON disconnect during unload")
                # If is_connected is sync and returns True, disconnect
                elif self.rcon_client.is_connected():
                    # Check if disconnect is async
                    if asyncio.iscoroutinefunction(self.rcon_client.disconnect):
                        logger.info("Creating task for async RCON disconnect")
                        loop = asyncio.get_event_loop()
                        # asyncなdisconnectを安全に呼び出す
                        # 変数を明示的に宣言して型注釈を追加
                        disconnect_task = loop.create_task(self.rcon_client.disconnect())  # type: ignore
                        # タスクを無視しているという意図を明示
                        _ = disconnect_task  # pyright: ignore[reportUnusedVariable]
                    else:
                        # Synchronous disconnect
                        self.rcon_client.disconnect() # type: ignore
            except Exception as e:
                logger.error(f"Error during RCON disconnect in unload: {e}")

        logger.info("ServerCog unloaded.")


# Setup function for loading the cog
def setup(bot: commands.Bot):
    """Loads the ServerCog."""
    try:
        bot.add_cog(ServerCog(bot))
        logger.info("ServerCog loaded successfully.")
    except Exception as e:
         logger.critical(f"Failed to load ServerCog: {e}", exc_info=True)
