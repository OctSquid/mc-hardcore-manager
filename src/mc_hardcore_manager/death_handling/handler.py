import logging
import discord
from discord.ext import commands
import asyncio
from typing import Optional, Dict # Added Dict

# Import components from their new locations
from ..config import Config, get_config # Assuming get_config is still valid or handled in main
from ..core.data_manager import DataManager, DataError # Import DataError
from ..core.exceptions import DeathHandlingError, WorldManagementError, RconError, OpenAIError # Import relevant exceptions
from ..minecraft.rcon_client import RconClient # RconClient is already correct
from ..minecraft.world_manager import WorldManager # WorldManager is already correct
from ..minecraft.scoreboard_manager import ScoreboardManager # Import ScoreboardManager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Import MCHardcoreBot for type checking only
    from .. import main
from ..minecraft.death_event_dispatcher import DeathEventDispatcher # Import the dispatcher
from .analyzer import DeathAnalyzer # Analyzer is already correct
from .actions import DeathAction # Action is already correct

logger = logging.getLogger(__name__)

class DeathHandler:
    """Handles the overall process when a player death is detected."""

    def __init__(self, bot: commands.Bot, config: Config, data_manager: DataManager, rcon_client: RconClient, world_manager: WorldManager, death_analyzer: DeathAnalyzer, death_action: DeathAction, death_event_dispatcher: Optional[DeathEventDispatcher] = None):
        # カスタムBotクラスを使用
        from typing import Any, cast
        if TYPE_CHECKING:
            from ..main import MCHardcoreBot
            self.bot = cast('MCHardcoreBot', bot)
        else:
            self.bot = bot
        # Inject dependencies
        self.config = config # Use injected config
        self.data_manager = data_manager
        self.rcon_client = rcon_client
        self.world_manager = world_manager
        self.death_analyzer = death_analyzer
        self.death_action = death_action
        self.notice_channel: Optional[discord.TextChannel] = None
        self.admin_channel: Optional[discord.TextChannel] = None
        # イベントディスパッチャーの追加、なければ新規作成
        self.death_event_dispatcher = death_event_dispatcher or DeathEventDispatcher(asyncio.get_event_loop())
        # 死亡アクションの実行フラグ（挑戦につき1回だけ実行するための制御）
        self.death_actions_executed = False
        logger.info(f"DeathHandler initialized with death_event_dispatcher: {self.death_event_dispatcher}")
        # Ensure world_manager knows about the admin channel if needed for its logging
        # Don't set admin channel yet, it will be set after initialization
        
    def reset_death_action_flags(self):
        """サーバー再起動時に死亡アクションフラグをリセットする"""
        self.death_actions_executed = False
        logger.info("Death action flags reset")

    async def initialize_channels(self):
        """Fetch and store channel objects."""
        try:
            # Fetch channels using IDs from the injected config object
            notice_channel_id = self.config.discord.notice_channel_id
            admin_channel_id = self.config.discord.admin_channel_id

            notice_channel = await self.bot.fetch_channel(notice_channel_id)
            admin_channel = await self.bot.fetch_channel(admin_channel_id)

            # Type checking after fetch
            if isinstance(notice_channel, discord.TextChannel):
                self.notice_channel = notice_channel
            else:
                logger.error(f"Notice channel ID {notice_channel_id} is not a valid TextChannel.")
                self.notice_channel = None # Reset if invalid type
                
            if isinstance(admin_channel, discord.TextChannel):
                 self.admin_channel = admin_channel
                 # Pass the fetched admin channel to WorldManager
                 self.world_manager.set_admin_channel(self.admin_channel)
                 logger.info(f"Admin channel set for WorldManager: {self.admin_channel.name}")
            else:
                logger.error(f"Admin channel ID {admin_channel_id} is not a valid TextChannel.")
                self.admin_channel = None # Reset if invalid type

        except discord.NotFound as e:
            logger.error(f"Could not find one or both Discord channels (Notice: {notice_channel_id}, Admin: {admin_channel_id}). Check IDs in config. Error: {e}")
        except discord.Forbidden as e:
            logger.error(f"Bot lacks permissions to fetch channels (Notice: {notice_channel_id}, Admin: {admin_channel_id}). Error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error fetching channels: {e}")

    async def handle_death(self, player_name: str, death_message: str, timestamp: str):
        """Processes a player death event."""
        logger.info(f"Processing death for player: {player_name}")
        logger.info(f"Handler initialized - Notice Channel: {self.notice_channel}, Admin Channel: {self.admin_channel}")

        # 重複実行防止チェック - すでに実行済みの場合は何もしない
        if self.death_actions_executed:
            logger.info(f"Death actions already executed for this challenge. Skipping for {player_name}'s death.")
            return
            
        # フラグを立てて処理開始（以降の死亡では実行されない）
        self.death_actions_executed = True
        logger.info(f"Executing death actions for the first time in this challenge (player: {player_name})")

        # チャンネル初期化
        if not self.notice_channel or not self.admin_channel:
            await self.initialize_channels()
            if not self.notice_channel or not self.admin_channel:
                logger.error("Cannot handle death notification/reset without valid channels.")
                # Send DM to owner as fallback
                try:
                    owner_id = self.config.discord.owner_ids[0]  # Get first owner
                    owner = await self.bot.fetch_user(owner_id)
                    await owner.send(
                        f"⚠️ チャンネル初期化エラー: 通知チャンネル({self.config.discord.notice_channel_id}) "
                        f"または管理チャンネル({self.config.discord.admin_channel_id})が見つかりません。"
                        "設定を確認してください。"
                    )
                except Exception as e:
                    logger.error(f"Failed to send DM to owner: {e}")
                return
                
        # --- 即時実行タスクをすぐに開始 ---
        # タイトル表示と効果音の非同期タスクを作成
        title_task = None
        sound_task = None
        
        # タイトル表示の実行（設定が有効な場合）
        if self.config.death_title.enabled:
            try:
                logger.info(f"Immediately showing death title for {player_name}")
                title_task = asyncio.create_task(self.death_action.show_death_title(player_name))
            except Exception as e:
                logger.error(f"Error creating title task: {e}")
                
        # 効果音の実行（設定が有効な場合）
        if self.config.death_sound.enabled:
            try:
                logger.info(f"Immediately playing death sound")
                sound_task = asyncio.create_task(self.death_action.play_death_sound())
            except Exception as e:
                logger.error(f"Error creating sound task: {e}")

        try:
            # --- Step 1: 今回の挑戦時間の計算のため、死亡前のワールド開始時間を取得 ---
            current_start_time = self.data_manager.get_start_time()
            
            # --- Step 2: ワールド開始からプレイヤー死亡までの経過時間を計算 ---
            if current_start_time:
                current_challenge_time_str = self.data_manager.get_elapsed_time_str(current_start_time)
            else:
                current_challenge_time_str = "記録なし"
                
            # --- Step 3: 統計情報を更新 ---
            stats = self.data_manager.increment_death_count(player_name)
            challenge_count = self.data_manager.get_challenge_count()
            player_death_count = self.data_manager.get_player_death_count(player_name)
            
            # --- スコアボードの更新 ---
            try:
                if hasattr(self.bot, 'scoreboard_manager') and self.bot.scoreboard_manager is not None:
                    logger.info(f"Updating scoreboard for player {player_name} with death count {player_death_count}")
                    # 特定のプレイヤーのスコアを更新
                    scoreboard_manager = getattr(self.bot, 'scoreboard_manager', None)
                    if scoreboard_manager is not None:
                        await scoreboard_manager.update_player_death_count(player_name, player_death_count)
                    else:
                        logger.warning(f"Scoreboard manager is not an instance of ScoreboardManager: {type(scoreboard_manager)}")
                else:
                    logger.warning("Scoreboard manager not found, scoreboard not updated")
            except Exception as e:
                logger.error(f"Error updating scoreboard: {e}", exc_info=True)
            
            # --- Step 4: 累計挑戦時間（1回目の挑戦がスタートしてからの時間）を取得 ---
            # 1回目のチャレンジなら累計時間はまだない（今回の挑戦時間をそのまま使用）
            if challenge_count == 1:
                total_challenge_time_str = current_challenge_time_str
                logger.info(f"初回チャレンジのため、今回の挑戦時間を累計挑戦時間として使用: {total_challenge_time_str}")
            else:
                total_challenge_time_str = self.data_manager.get_total_elapsed_time_str()
            
            logger.info(f"今回の挑戦時間: {current_challenge_time_str}, 累計挑戦時間: {total_challenge_time_str}")


            # --- Step 2: Analyze Death Cause (using OpenAI) ---
            death_analysis = {
                "summary": "死亡", 
                "description": f"死因: `{death_message}`"
            }  # Default values
            
            try:
                # Pass only the raw message, analyzer extracts player name if needed
                death_analysis = await self.death_analyzer.analyze_death_cause(death_message)
            except OpenAIError as e:
                 logger.error(f"OpenAI error analyzing death cause for {player_name}: {e}")
                 # Use a fallback description indicating the error
                 death_analysis["description"] = f"死因: `{death_message}`\n\n_(AIによる説明生成中にエラーが発生しました)_"
            except Exception as e:
                 logger.error(f"Unexpected error analyzing death cause for {player_name}: {e}", exc_info=True)
                 death_analysis["description"] = f"死因: `{death_message}`\n\n_(死因分析中に予期せぬエラーが発生しました)_"


            # --- Step 3: Notify Discord (Public Channel) ---
            await self._send_death_notification(
                player_name, death_analysis, player_death_count, challenge_count, 
                current_challenge_time_str, total_challenge_time_str
            )

            # --- Step 4: Trigger Death Actions (e.g., explosion) ---
            if self.config.death_explosion.enabled:
                try:
                    # Coordinates are not strictly needed if using 'execute at' in DeathAction
                    await self.death_action.trigger_explosion_on_others(player_name)
                except RconError as e:
                     logger.error(f"RconError during death explosion action for {player_name}: {e}")
                except Exception as e:
                     logger.error(f"Unexpected error during death explosion action for {player_name}: {e}", exc_info=True)
            else:
                 logger.debug("Death explosion action disabled.")


            # --- Step 5: Ask for World Reset Confirmation (Admin Channel) ---
            await self._request_world_reset(player_name)
            
            # --- Step 6: Dispatch event to registered handlers ---
            logger.info(f"Dispatching death event to registered handlers for {player_name}")
            await self.death_event_dispatcher.dispatch_death_event(player_name, death_message, timestamp)

        except DataError as e:
             logger.critical(f"DataError handling death for {player_name}: {e}", exc_info=True)
             # Notify admin if possible
             if self.admin_channel:
                 await self.admin_channel.send(f"🚨 **重大なエラー:** {player_name} の死亡処理中に統計データの読み書きに失敗しました。データが破損している可能性があります。\nエラー: {e}")
        except Exception as e:
            logger.error(f"Unhandled error in handle_death for {player_name}: {e}", exc_info=True)
            if self.admin_channel:
                try:
                    await self.admin_channel.send(f"⚠️ **エラー:** {player_name} の死亡イベント処理中に予期せぬエラーが発生しました。\n```{type(e).__name__}: {e}```")
                except Exception as send_e:
                     logger.error(f"Failed to send error message to admin channel: {send_e}")


    async def _send_death_notification(self, player_name: str, cause_info: Dict[str, str], death_count: int, challenge_count: int, current_challenge_time: str, total_challenge_time: str):
        """Sends the formatted death message to the notice channel."""
        # チャンネルが初期化されていない場合は再初期化を試みる
        if not self.notice_channel:
            logger.warning("Notice channel not available, attempting to initialize channels")
            await self.initialize_channels()
            
        if not self.notice_channel:
            logger.error("Notice channel not available for sending death notification after initialization attempt.")
            
            # 通知が失敗した場合はオーナーにDMを送信する
            try:
                owner_id = self.config.discord.owner_ids[0]  # 最初のオーナーIDを取得
                owner = await self.bot.fetch_user(owner_id)
                await owner.send(
                    f"⚠️ **エラー**: {player_name}の死亡通知を送信できませんでした。通知チャンネル({self.config.discord.notice_channel_id})が見つかりません。"
                    "設定を確認してください。"
                )
                logger.info(f"Fallback DM sent to owner {owner_id} about missing notice channel")
            except Exception as e:
                logger.error(f"Failed to send DM to owner about notice channel issue: {e}")
                
            return

        # Extract summary and description from cause_info dictionary
        summary = cause_info.get("summary", "死亡")
        description = cause_info.get("description", f"{player_name}が死亡しました")
        
        embed = discord.Embed(
            title=f"{player_name} が死亡しました！",  # Use the short summary in the title
            description=f"""
            死因: `{summary}`\n
            {description}
            """,  # Use the detailed AI-enhanced description
            color=discord.Color.red()
        )
        # Use Minotar API for face icon
        face_url = f"https://minotar.net/avatar/{player_name}/64.png" # Smaller icon size
        embed.set_author(name=player_name, icon_url=face_url)
        embed.add_field(name="累計死亡回数", value=f"{death_count} 回", inline=True)
        embed.add_field(name="挑戦回数", value=f"{challenge_count} 回目", inline=True)
        embed.add_field(name="今回の挑戦時間", value=current_challenge_time, inline=True)
        embed.add_field(name="累計挑戦時間", value=total_challenge_time, inline=True)
        embed.set_footer(text="新たな挑戦が始まります...") # Footer text
        embed.timestamp = discord.utils.utcnow() # Add timestamp

        try:
            message = await self.notice_channel.send(embed=embed)
            logger.info(f"Sent death notification for {player_name} to {self.notice_channel.name}, message ID: {message.id}")
            return True  # 通知が成功したことを明示的に示す
        except discord.Forbidden as e:
            logger.error(f"Bot lacks permission to send messages in {self.notice_channel.name}: {e}")
        except discord.HTTPException as e:
            logger.error(f"HTTP error sending death notification: {e.status} {e.text}")
        except Exception as e:
            logger.error(f"Failed to send death notification: {type(e).__name__}: {e}", exc_info=True)
            
        # エラーが発生した場合はオーナーにDMを送信
        try:
            owner_id = self.config.discord.owner_ids[0]
            owner = await self.bot.fetch_user(owner_id)
            await owner.send(
                f"⚠️ **エラー**: {player_name}の死亡通知をDiscordチャンネルに送信できませんでした。\n"
                f"チャンネル: {self.notice_channel.name if self.notice_channel else 'なし'}\n"
                "ボットの権限設定を確認してください。"
            )
            logger.info(f"Sent fallback DM to owner about notification failure")
        except Exception as dm_error:
            logger.error(f"Failed to send DM to owner about notification failure: {dm_error}")
            
        return False  # 通知が失敗したことを示す

    # _get_player_coordinates removed

    async def _request_world_reset(self, player_name: str):
        """Sends a confirmation message with buttons to the admin channel for world reset."""
        if not self.admin_channel:
            logger.error("Admin channel not available for requesting world reset.")
            return

        try:
            # Import the view here to avoid potential circular imports at module level
            # Ensure the view is compatible with the new WorldManager structure
            from ..discord_bot.views.death_reset_confirmation_view import DeathResetConfirmationView

            # Pass the WorldManager instance to the view
            view = DeathResetConfirmationView(self.world_manager)
            embed = discord.Embed(
                title="プレイヤー死亡 - ワールドリセット確認",
                description=f"{player_name} が死亡しました。サーバーを停止し、ワールドをリセットして再起動しますか？\n**この操作は元に戻せません！**",
                color=discord.Color.orange()
            )
            embed.set_footer(text="下のボタンで操作を選択してください。")

            message = await self.admin_channel.send(embed=embed, view=view)
            logger.info(f"Sent world reset confirmation request to {self.admin_channel.name}")

            # The view now handles waiting and disabling itself internally
            # await view.wait() # No longer needed here if view handles it
            # await message.edit(view=None) # View should disable items on completion/timeout

        except ImportError:
             logger.error("Could not import DeathResetConfirmationView. Reset confirmation cannot be sent.")
             await self.admin_channel.send("⚠️ エラー: ワールドリセット確認UIの読み込みに失敗しました。")
        except discord.Forbidden:
            logger.error(f"Bot lacks permission to send messages or use components in {self.admin_channel.name}")
            # Try sending a plain text message as fallback?
            await self.admin_channel.send(f"⚠️ {player_name} が死亡しました。ワールドリセットが必要です。\n(ボタン表示権限がないため、手動で `/resetworld` コマンドを実行してください)")
        except Exception as e:
            logger.error(f"Failed to send world reset confirmation: {e}", exc_info=True)
            await self.admin_channel.send(f"⚠️ ワールドリセット確認の送信中にエラーが発生しました: {e}")
