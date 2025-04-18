import logging
import asyncio
from typing import Dict, Optional

# Import RconClient and RconError from the correct location
from ..minecraft.rcon_client import RconClient, RconError
from ..core.exceptions import DeathHandlingError # Optional: for wrapping errors
from ..config import Config

logger = logging.getLogger(__name__)

class DeathAction:
    """Handles actions to be taken upon player death, like explosions, titles, and sounds."""

    def __init__(self, rcon_client: RconClient, config: Config):
        self.rcon_client = rcon_client
        self.config = config
        self.explosion_delay_seconds = config.death_explosion.delay
        self.fuse_ticks = max(0, self.explosion_delay_seconds * 20) # Ensure non-negative ticks

    async def trigger_explosion_on_others(
        self,
        dead_player_name: str,
        # Coordinates might be needed if 'execute at' isn't reliable or desired
        # dead_player_coords: Optional[Dict[str, float]] = None
    ):
        """
        Triggers TNT explosion on all other online players via RCON.

        Args:
            dead_player_name: The name of the player who died (to exclude them).
            # dead_player_coords: Optional coordinates of the dead player.
        """
        logger.info(f"Attempting death explosion for players other than {dead_player_name} with {self.explosion_delay_seconds}s delay.")

        try:
            # Ensure RCON is connected (connect raises RconError)
            await self.rcon_client.connect()

            # Get list of online players using the async command method
            list_response = await self.rcon_client.command("list")
            if not list_response or ":" not in list_response:
                logger.warning(f"Could not get player list for death explosion. RCON response: '{list_response}'")
                return # Cannot proceed without player list

            # Parse player list (handle potential "0 players online" case)
            player_list_part = list_response.split(":", 1)[1].strip()
            if not player_list_part:
                 online_players = []
                 logger.info("No other players online to explode.")
                 return
            else:
                 online_players = [p.strip() for p in player_list_part.split(",") if p.strip()]


            exploded_count = 0
            for player in online_players:
                if player == dead_player_name:
                    continue # Don't explode the player who just died

                logger.info(f"Summoning TNT for player {player} with {self.explosion_delay_seconds}s delay ({self.fuse_ticks} ticks).")
                # Command to summon primed TNT at the player's location
                # Using 'execute at' is generally preferred
                command = f"execute at {player} run summon minecraft:tnt ~ ~ ~ {{Fuse:{self.fuse_ticks}}}"
                # Alternative if coords are provided:
                # if dead_player_coords:
                #     x, y, z = dead_player_coords['x'], dead_player_coords['y'], dead_player_coords['z']
                #     command = f"summon minecraft:tnt {x} {y} {z} {{Fuse:{self.fuse_ticks}}}"
                # else: # Fallback if coords missing
                #     command = f"execute at {player} run summon minecraft:tnt ~ ~ ~ {{Fuse:{self.fuse_ticks}}}"

                try:
                    tnt_response = await self.rcon_client.command(command)
                    # Check response? TNT summon usually returns success message or error
                    if "Summoned new tnt" in tnt_response: # Example check
                         logger.debug(f"Successfully summoned TNT for {player}. Response: {tnt_response}")
                         exploded_count += 1
                    else:
                         logger.warning(f"TNT summon command for {player} might have failed. Response: {tnt_response}")
                    await asyncio.sleep(0.1) # Small delay between commands to avoid spamming server
                except RconError as e:
                     logger.error(f"RCON error summoning TNT for player {player}: {e}")
                     # Decide whether to continue with other players or stop
                     continue # Continue trying for other players

            logger.info(f"Death explosion sequence complete. TNT summoned for {exploded_count} players.")

        except RconError as e:
            # Error connecting or getting player list
            logger.error(f"RCON error during death explosion sequence: {e}")
            # Optionally raise a DeathHandlingError
            # raise DeathHandlingError(f"RCON error during explosion: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during death explosion sequence: {e}", exc_info=True)
            # Optionally raise a DeathHandlingError
            # raise DeathHandlingError(f"Unexpected error during explosion: {e}") from e
        finally:
            if await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()
                 
    async def show_death_title(self, player_name: str):
        """死亡メッセージを全プレイヤーにタイトル表示する"""
        if not self.config.death_title.enabled:
            logger.info("Death title display is disabled in config.")
            return
            
        logger.info(f"Displaying death title for player {player_name}'s death")
        
        try:
            # Ensure RCON is connected
            await self.rcon_client.connect()
            
            # タイトルタイミングの設定
            timing_command = f"title @a times {self.config.death_title.fade_in} {self.config.death_title.stay} {self.config.death_title.fade_out}"
            await self.rcon_client.command(timing_command)
            
            # タイトルを表示（赤色、太字）
            title_command = f'title @a title {{"text":"挑戦失敗！","color":"red"}}'
            await self.rcon_client.command(title_command)
            
            # サブタイトル表示
            subtitle_command = f'title @a subtitle {{"text":"{player_name} が死亡しました","color":"white"}}'
            await self.rcon_client.command(subtitle_command)
            
            logger.info(f"Death title displayed for player {player_name}")
        except RconError as e:
            logger.error(f"RCON error during title display: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during title display: {e}", exc_info=True)
        finally:
            if await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()
                
    async def play_death_sound(self):
        """死亡時の効果音を全プレイヤーに再生する"""
        if not self.config.death_sound.enabled:
            logger.info("Death sound is disabled in config.")
            return
            
        logger.info("Playing death sound for all players")
        
        try:
            # Ensure RCON is connected
            await self.rcon_client.connect()
            
            # 全プレイヤーに効果音を再生
            sound_command = f"execute at @a run playsound {self.config.death_sound.sound_id} master @a ~ ~ ~ {self.config.death_sound.volume} {self.config.death_sound.pitch}"
            await self.rcon_client.command(sound_command)
            
            logger.info("Death sound played for all players")
        except RconError as e:
            logger.error(f"RCON error during sound playback: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during sound playback: {e}", exc_info=True)
        finally:
            if await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()
