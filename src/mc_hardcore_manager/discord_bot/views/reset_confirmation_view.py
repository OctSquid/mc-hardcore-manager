import discord
from discord.ui import View, Button, button
from discord import ButtonStyle, Interaction
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)

class ResetConfirmationView(View):
    """
    A Discord UI View component for confirming a potentially destructive action like world reset.
    """
    def __init__(self, interaction_check: Callable[[Interaction], bool], timeout=60):
        """
        Initializes the confirmation view.

        Args:
            interaction_check: A callable that takes an Interaction and returns True if the user
                               is allowed to interact, False otherwise.
            timeout: How long the view should wait for interaction before timing out (seconds).
        """
        super().__init__(timeout=timeout)
        self._interaction_check = interaction_check
        self.confirmed: Optional[bool] = None # None = timeout, True = confirmed, False = cancelled
        self.message: Optional[discord.Message] = None # Store the message this view is attached to

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Checks if the interacting user is allowed to use the buttons."""
        allowed = self._interaction_check(interaction)
        if not allowed:
             # Send an ephemeral message if the user is not allowed
             try:
                 await interaction.response.send_message("この操作を実行する権限がありません。", ephemeral=True)
             except discord.InteractionResponded: # Handle case where response was already sent (e.g., by another check)
                 await interaction.followup.send("この操作を実行する権限がありません。", ephemeral=True)
             except Exception as e:
                  logger.error(f"Error sending interaction check failure message: {e}")
        return allowed

    @button(label="はい、リセットします", style=ButtonStyle.danger, custom_id="confirm_reset_action")
    async def confirm_button_callback(self, button_obj: Button, interaction: Interaction):
        """Callback for the confirmation button."""
        self.confirmed = True
        # Disable buttons immediately (型安全なアクセス)
        from discord.ui import Item
        for child in self.children:
            if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                # pyright: ignore[reportAttributeAccessIssue]
                child.disabled = True  # type: ignore
        # Edit the original message to show processing and disable buttons
        try:
            await interaction.response.edit_message(content="処理を開始します...", view=self)
        except Exception as e:
             logger.error(f"Error editing message on confirm: {e}")
             # Attempt followup if edit fails
             try:
                 await interaction.followup.send("処理を開始します...", ephemeral=True)
             except Exception as followup_e:
                  logger.error(f"Error sending followup on confirm: {followup_e}")
        self.stop() # Stop the view from listening further

    @button(label="キャンセル", style=ButtonStyle.secondary, custom_id="cancel_reset_action")
    async def cancel_button_callback(self, button_obj: Button, interaction: Interaction):
        """Callback for the cancellation button."""
        self.confirmed = False
        # Disable buttons immediately (型安全なアクセス)
        from discord.ui import Item
        for child in self.children:
            if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                # pyright: ignore[reportAttributeAccessIssue]
                child.disabled = True  # type: ignore
        try:
            await interaction.response.edit_message(content="処理はキャンセルされました。", view=self)
        except Exception as e:
             logger.error(f"Error editing message on cancel: {e}")
             try:
                 await interaction.followup.send("処理はキャンセルされました。", ephemeral=True)
             except Exception as followup_e:
                  logger.error(f"Error sending followup on cancel: {followup_e}")
        self.stop()

    async def on_timeout(self):
        """Called when the view times out."""
        self.confirmed = None # Explicitly set to None on timeout
        logger.warning("Reset confirmation view timed out.")
        # Disable buttons safely
        from discord.ui import Item
        for child in self.children:
            if isinstance(child, discord.ui.Button) or hasattr(child, 'disabled'):
                # pyright: ignore[reportAttributeAccessIssue]
                child.disabled = True  # type: ignore
        # Try to edit the original message to indicate timeout
        if self.message:
            try:
                await self.message.edit(content="確認がタイムアウトしました。", view=self)
            except discord.NotFound:
                 logger.warning("Original confirmation message not found on timeout.")
            except discord.Forbidden:
                 logger.error("Bot lacks permissions to edit the confirmation message on timeout.")
            except Exception as e:
                 logger.error(f"Error editing confirmation message on timeout: {e}", exc_info=True)
        else:
             logger.warning("No message reference stored in view for timeout edit.")
