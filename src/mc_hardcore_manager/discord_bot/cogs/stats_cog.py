from typing import Optional
import discord
from discord.ext import commands
from discord import slash_command, ApplicationContext, Interaction # Import necessary types
import logging

# Import components from new locations
from ...config import Config # Import the Config model
from ...core.data_manager import DataManager # Import the DataManager class
from ...core.exceptions import DataError # Import custom exception

logger = logging.getLogger(__name__)

class StatsCog(commands.Cog):
    """Cog for managing and displaying player statistics."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Get dependencies from bot instance (assuming they are attached in main.py)
        self.config = getattr(bot, 'config', None)  # 型アノテーションを削除して初期化時のエラーを回避
        self.data_manager = getattr(bot, 'data_manager', None)

        if not self.config or not self.data_manager:
             # This should not happen if main.py setup is correct
             logger.critical("StatsCog could not find required config or data_manager on bot instance!")
             raise RuntimeError("StatsCog failed to initialize dependencies")

        logger.info("StatsCog initialized.")

    # _save_stats method removed as DataManager handles saving internally

    @slash_command(name="stats", description="現在の挑戦回数とプレイヤーの死亡回数を表示します。")
    async def show_stats(self, ctx: ApplicationContext):
        """Displays the current challenge attempts and player death counts."""
        try:
            # データマネージャーのNoneチェック
            if self.data_manager is None:
                raise RuntimeError("DataManager is not available")
                
            # Get current stats directly from the DataManager instance
            stats_data = self.data_manager.get_all_stats()
            attempts = stats_data.get("challenge_count", 0)
            players_stats = stats_data.get("players", {})
            start_time_iso = stats_data.get("current_challenge_start_time")
            elapsed_time_str = self.data_manager.get_elapsed_time_str(start_time_iso)

        except DataError as e:
             logger.error(f"Failed to load stats data for /stats command: {e}", exc_info=True)
             await ctx.respond("統計データの読み込み中にエラーが発生しました。", ephemeral=True)
             return
        except Exception as e:
             logger.error(f"Unexpected error in /stats command: {e}", exc_info=True)
             await ctx.respond("統計情報の表示中に予期せぬエラーが発生しました。", ephemeral=True)
             return

        embed = discord.Embed(
            title="📊 ハードコアチャレンジ統計",
            description=f"現在の挑戦: **{attempts}** 回目", # Combine attempts into description
            color=discord.Color.blue()
        )
        embed.add_field(name="現在の挑戦 開始時刻", value=start_time_iso if start_time_iso else "N/A", inline=False)
        embed.add_field(name="現在の挑戦 経過時間", value=elapsed_time_str, inline=False)


        if players_stats:
            stats_lines = []
            # Sort players alphabetically for consistent display
            for player_name, player_data in sorted(players_stats.items()):
                # Use the get_player_death_count method for safety, though direct access is fine here
                deaths = player_data.get("death_count", 0)
                stats_lines.append(f"**{player_name}**: {deaths} 回")
            embed.add_field(name="プレイヤー別 累計死亡回数", value="\n".join(stats_lines) if stats_lines else "記録なし", inline=False)
        else:
            embed.add_field(name="プレイヤー別 累計死亡回数", value="まだ記録がありません。", inline=False)

        embed.set_footer(text="mc-hardcore-manager")
        embed.timestamp = discord.utils.utcnow()
        await ctx.respond(embed=embed)
        logger.info(f"Displayed stats for request by {ctx.author.name} ({ctx.author.id})")

    @slash_command(name="resetstats", description="全ての統計情報（挑戦回数、死亡回数）をリセットします。(オーナー限定)")
    @commands.is_owner() # Restrict to bot owners defined in config
    async def reset_stats_command(self, ctx: ApplicationContext):
        """Resets all statistics after confirmation."""
        
        # データマネージャーのNoneチェック
        if self.data_manager is None:
            await ctx.respond("統計データマネージャーが利用できないため、リセットできません。", ephemeral=True)
            logger.error("DataManager is None in reset_stats_command")
            return

        # --- Confirmation View (remains mostly the same, but uses self.data_manager) ---
        class ConfirmationView(discord.ui.View):
            def __init__(self, data_manager_instance: Optional[DataManager]):
                super().__init__(timeout=30.0)
                # Data managerはすでにself.data_manager is Noneチェックを実施済み
                self.data_manager = data_manager_instance 
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

            @discord.ui.button(label="はい、リセットします", style=discord.ButtonStyle.danger, custom_id="confirm_reset")
            async def confirm_button(self, button: discord.ui.Button, interaction: Interaction):
                self.confirmed = True
                self.stop()
                # childrenの各アイテムを型情報付きで処理
                from discord.ui import Item
                for child in self.children:
                    if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                        # pyright: ignore[reportAttributeAccessIssue]
                        child.disabled = True  # type: ignore
                # Use interaction response to edit the original message
                await interaction.response.edit_message(content="統計情報をリセットしています...", view=self)

            @discord.ui.button(label="キャンセル", style=discord.ButtonStyle.secondary, custom_id="cancel_reset")
            async def cancel_button(self, button: discord.ui.Button, interaction: Interaction):
                self.confirmed = False
                self.stop()
                # childrenの各アイテムを型情報付きで処理
                from discord.ui import Item
                for child in self.children:
                    if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                        # pyright: ignore[reportAttributeAccessIssue]
                        child.disabled = True  # type: ignore
                await interaction.response.edit_message(content="リセットはキャンセルされました。", view=self)

            async def on_timeout(self):
                self.confirmed = None # Explicitly set confirmed to None on timeout
                # childrenの各アイテムを型情報付きで処理
                from discord.ui import Item
                for child in self.children:
                    if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                        # pyright: ignore[reportAttributeAccessIssue]
                        child.disabled = True  # type: ignore
                # Try editing the original interaction response message
                if self.interaction_message:
                    try:
                        await self.interaction_message.edit(content="リセット確認がタイムアウトしました。", view=self)
                    except discord.NotFound:
                        logger.warning("Original reset confirmation message not found on timeout.")
                    except Exception as e:
                         logger.error(f"Error editing message on timeout: {e}")


        view = ConfirmationView(self.data_manager)
        # Respond ephemerally first
        await ctx.respond(
            "⚠️ **警告:** 本当に全ての統計情報（挑戦回数と全プレイヤーの死亡回数）をリセットしますか？\n**この操作は元に戻せません！**",
            view=view,
            ephemeral=True
        )
        # Store the interaction message for timeout handling
        view.interaction_message = await ctx.interaction.original_response()


        # Wait for the view to stop
        await view.wait()

        # Follow up based on the result (use followup for ephemeral responses)
        if view.confirmed is True:
            try:
                # 再度data_managerのNoneチェック
                if self.data_manager is None:
                    await ctx.followup.send("❌ 統計データマネージャーが利用できないため、リセットできません。", ephemeral=True)
                    logger.error("DataManager is None when trying to reset_stats")
                    return
                
                self.data_manager.reset_stats() # Call the method on the instance
                await ctx.followup.send("✅ 統計情報は正常にリセットされました。", ephemeral=True)
                logger.warning(f"Stats reset initiated by owner {ctx.author.name} ({ctx.author.id})")
            except DataError as e:
                 logger.error(f"DataError resetting stats: {e}", exc_info=True)
                 await ctx.followup.send(f"❌ 統計情報のリセット中にデータエラーが発生しました: {e}", ephemeral=True)
            except Exception as e:
                logger.error(f"Unexpected error resetting stats: {e}", exc_info=True)
                await ctx.followup.send("❌ 統計情報のリセット中に予期せぬエラーが発生しました。", ephemeral=True)
        elif view.confirmed is False:
             # Cancel message already sent by button interaction
             await ctx.followup.send("リセットはキャンセルされました。", ephemeral=True) # Send followup for clarity
        else: # Timeout case
             # Timeout message already sent by on_timeout
             await ctx.followup.send("リセット確認がタイムアウトしました。", ephemeral=True) # Send followup for clarity


    @reset_stats_command.error
    async def reset_stats_error(self, ctx: ApplicationContext, error):
        """Error handler for the resetstats command."""
        if isinstance(error, commands.NotOwner):
            await ctx.respond("このコマンドはBotのオーナーのみが実行できます。", ephemeral=True)
            logger.warning(f"Unauthorized resetstats attempt by {ctx.author.name} ({ctx.author.id})")
        elif isinstance(error, commands.CheckFailure): # Catch other potential check failures
             await ctx.respond("コマンドの実行権限がありません。", ephemeral=True)
             logger.warning(f"Check failure for resetstats by {ctx.author.name} ({ctx.author.id}): {error}")
        else:
            await ctx.respond(f"コマンドの実行中に予期せぬエラーが発生しました。", ephemeral=True)
            logger.error(f"Error in resetstats command by {ctx.author.name}: {error}", exc_info=True)


# setup function remains largely the same, but doesn't need config passed to Cog constructor
def setup(bot: commands.Bot):
    """Loads the StatsCog."""
    # Dependencies (config, data_manager) are expected to be attached to bot instance before loading
    try:
        bot.add_cog(StatsCog(bot))
        logger.info("StatsCog loaded successfully.")
    except Exception as e:
         logger.critical(f"Failed to load StatsCog: {e}", exc_info=True)
         # Depending on severity, you might want to prevent the bot from starting
