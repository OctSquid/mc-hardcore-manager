import discord
from discord.ui import View, Button, button
from discord import ButtonStyle, Interaction
import logging
import asyncio # Keep asyncio if needed for create_task, though direct await might be better
from typing import Optional

# Import WorldManager and its exception
from ...minecraft.world_manager import WorldManager, WorldManagementError

logger = logging.getLogger(__name__)

class DeathResetConfirmationView(View):
    """
    View with buttons for confirming or cancelling world reset after a player death.
    Initiates the reset process via the WorldManager.
    """
    def __init__(self, world_manager: WorldManager, timeout=300.0): # Timeout in seconds (5 minutes)
        """
        Initializes the view.

        Args:
            world_manager: The instance of WorldManager to perform the reset.
            timeout: How long the view should wait for interaction (seconds).
        """
        super().__init__(timeout=timeout)
        self.world_manager = world_manager
        self.confirmed: Optional[bool] = None
        self.message: Optional[discord.Message] = None # To store the message this view is attached to

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Checks if the interacting user is an owner."""
        # interaction.clientとinteraction.userがともにNoneでないことを確認
        if interaction.client is None:
            logger.warning("Interaction check failed: Client is None.")
            return False
            
        if not interaction.client.is_ready(): # Ensure bot is ready before accessing owner_ids
             logger.warning("Interaction check failed: Bot is not ready.")
             await interaction.response.send_message("Botがまだ準備中です。少し待ってから再試行してください。", ephemeral=True)
             return False

        # interaction.userがNoneでないことを確認
        if interaction.user is None:
            logger.warning("Interaction check failed: User is None.")
            return False

        # owner_idsがClientに存在するか確認（ない場合は空リストと見なす）
        owner_ids = getattr(interaction.client, 'owner_ids', [])
        allowed = interaction.user.id in owner_ids
        if not allowed:
             try:
                 # Use interaction.response for the first response
                 await interaction.response.send_message("この操作はBotのオーナーのみが実行できます。", ephemeral=True)
             except discord.InteractionResponded:
                 # If already responded (e.g., defer), use followup
                 await interaction.followup.send("この操作はBotのオーナーのみが実行できます。", ephemeral=True)
             except Exception as e:
                  logger.error(f"Error sending owner check failure message: {e}")
             # 安全なログ出力（user.nameとuser.idがあることを確認）
             user_info = f"{getattr(interaction.user, 'name', 'unknown')} ({getattr(interaction.user, 'id', 'unknown')})"
             logger.warning(f"Unauthorized death reset confirmation attempt by {user_info}")
        return allowed

    @button(label="ワールド再生成して再起動", style=ButtonStyle.danger, custom_id="confirm_death_world_reset")
    async def confirm_button(self, button_obj: Button, interaction: Interaction):
        """Callback for the confirmation button. Edits message and starts reset."""
        self.confirmed = True
        self.stop() # Stop listening for further interactions on this view
        
        # Disable buttons immediately (型安全なアクセス)
        from discord.ui import Item
        for child in self.children:
            if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                # pyright: ignore[reportAttributeAccessIssue]
                child.disabled = True  # type: ignore

        # interaction.userのNull安全性確保
        user_name = "不明なユーザー"
        if interaction.user is not None:
            user_name = getattr(interaction.user, 'name', "不明なユーザー")

        # Acknowledge interaction and update message
        try:
            await interaction.response.edit_message(
                content=f"✅ **{user_name}** がワールドの再生成を承認しました。処理を開始します...\n_(進捗はAdminチャンネルに通知されます)_", 
                view=self
            )
        except Exception as e:
             logger.error(f"Error editing message on death reset confirm: {e}")
             # Attempt followup if edit fails, but don't block reset
             try:
                 await interaction.followup.send("処理を開始します...", ephemeral=True)
             except Exception as followup_e:
                  logger.error(f"Error sending followup on death reset confirm: {followup_e}")

        # --- Trigger the actual reset process ---
        # Run execute_world_reset in the background so the interaction doesn't time out
        # The reset function itself sends progress updates to the admin channel
        logger.info(f"Creating task for execute_world_reset triggered by {user_name}")
        asyncio.create_task(self._run_reset_and_handle_errors(interaction))


    async def _run_reset_and_handle_errors(self, interaction: Interaction):
         """Wrapper to run the reset and handle potential errors, reporting back."""
         # interaction.userのNull安全性確保
         user_name = "不明なユーザー"
         if interaction.user is not None:
            user_name = getattr(interaction.user, 'name', "不明なユーザー")
             
         try:
              success = await self.world_manager.execute_world_reset()
              # Optionally send a final status via followup if needed, though WorldManager logs to admin channel
              # if success:
              #      await interaction.followup.send("ワールドリセット処理が完了しました。", ephemeral=True)
              # else:
              #      await interaction.followup.send("ワールドリセット処理中にエラーが発生しました。", ephemeral=True)
         except WorldManagementError as e:
              logger.critical(f"WorldManagementError during reset triggered by {user_name}: {e}", exc_info=True)
              try:
                   await interaction.followup.send(f"❌ ワールドリセット処理中に致命的なエラーが発生しました: {e}", ephemeral=True)
              except Exception as report_e:
                   logger.error(f"Failed to report WorldManagementError via followup: {report_e}")
         except Exception as e:
              logger.critical(f"Unexpected critical error during reset triggered by {user_name}: {e}", exc_info=True)
              try:
                   await interaction.followup.send(f"❌ ワールドリセット処理中に予期せぬ重大なエラーが発生しました。", ephemeral=True)
              except Exception as report_e:
                   logger.error(f"Failed to report unexpected critical error via followup: {report_e}")


    @button(label="キャンセル", style=ButtonStyle.secondary, custom_id="cancel_death_world_reset")
    async def cancel_button(self, button_obj: Button, interaction: Interaction):
        """Callback for the cancellation button."""
        self.confirmed = False
        self.stop()
        
        # Disable buttons immediately (型安全なアクセス)
        from discord.ui import Item
        for child in self.children:
            if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                # pyright: ignore[reportAttributeAccessIssue]
                child.disabled = True  # type: ignore
                
        # interaction.userのNull安全性確保
        user_name = "不明なユーザー"
        if interaction.user is not None:
            user_name = getattr(interaction.user, 'name', "不明なユーザー")
            
        try:
            await interaction.response.edit_message(content=f"❌ **{user_name}** がワールドの再生成をキャンセルしました。", view=self)
        except Exception as e:
             logger.error(f"Error editing message on death reset cancel: {e}")
             # Attempt followup if edit fails
             try:
                 await interaction.followup.send("ワールドの再生成はキャンセルされました。", ephemeral=True)
             except Exception as followup_e:
                  logger.error(f"Error sending followup on death reset cancel: {followup_e}")
        logger.info(f"Death world reset cancelled by {user_name}")

    async def on_timeout(self):
        """Called when the view times out."""
        # Check if interaction already happened
        if self.is_finished():
             return

        self.confirmed = None # Indicate timeout explicitly
        logger.warning("Death world reset confirmation timed out.")
        
        # Disable buttons immediately (型安全なアクセス)
        from discord.ui import Item
        for child in self.children:
            if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                # pyright: ignore[reportAttributeAccessIssue]
                child.disabled = True  # type: ignore
        # Try to edit the original message if we stored it
        if self.message:
            try:
                await self.message.edit(content="ワールド再生成の確認がタイムアウトしました。", view=self)
            except discord.NotFound:
                 logger.warning("Original death reset confirmation message not found on timeout.")
            except discord.Forbidden:
                 logger.error("Bot lacks permissions to edit the death reset confirmation message on timeout.")
            except Exception as e:
                 logger.error(f"Error editing death reset confirmation message on timeout: {e}", exc_info=True)
        else:
             # This case might happen if the initial message sending failed or wasn't stored
             logger.warning("No message reference stored in death reset view for timeout edit.")
