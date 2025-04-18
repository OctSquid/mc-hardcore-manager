import logging
from typing import Dict, Any

from ..minecraft.rcon_client import RconClient, RconError
from ..core.data_manager import DataManager
from ..config import Config

logger = logging.getLogger(__name__)

class ScoreboardManager:
    """Minecraftのスコアボードを管理するクラス"""

    def __init__(self, rcon_client: RconClient, config: Config):
        self.rcon_client = rcon_client
        self.config = config
        
    async def init_death_count_scoreboard(self, manage_connection: bool = True):
        """
        死亡回数スコアボードを初期化する
        
        Args:
            manage_connection: Trueの場合、このメソッド内でRCONの接続/切断を行う。
                              Falseの場合、呼び出し元で接続/切断を管理する
        """
        connected_here = False
        try:
            if manage_connection and not await self.rcon_client.is_connected():
                await self.rcon_client.connect()
                connected_here = True
            
            # スコアボードが存在するか確認（既存の場合はエラーになるが無視）
            try:
                response = await self.rcon_client.command('scoreboard objectives add deaths dummy "死亡回数"')
                logger.info(f"死亡回数スコアボードを作成しました: {response}")
            except RconError as e:
                if "already exists" in str(e) or "already exists" in str(e).lower():
                    logger.info("死亡回数スコアボードは既に存在します")
                else:
                    # その他のエラーは再スロー
                    raise
            
            # 確実にサイドバーに表示する
            response = await self.rcon_client.command('scoreboard objectives setdisplay sidebar deaths')
            logger.info(f"死亡回数スコアボードをサイドバーに表示しました: {response}")

            # 体力スコアボードの初期化（存在しない場合のみ作成）
            try:
                response = await self.rcon_client.command('scoreboard objectives add health health')
                logger.info(f"体力スコアボードを作成しました: {response}")
            except RconError as e:
                if "already exists" in str(e) or "already exists" in str(e).lower():
                    logger.info("体力スコアボードは既に存在します")
                else:
                    raise # その他のRCONエラーは再スロー

            # 体力スコアボードの表示設定
            response = await self.rcon_client.command('scoreboard objectives modify health rendertype hearts')
            logger.info(f"体力スコアボードの表示タイプをheartsに設定しました: {response}")
            response = await self.rcon_client.command('scoreboard objectives setdisplay list health')
            logger.info(f"体力スコアボードをリスト表示に設定しました: {response}")

            # 念のため状態を確認
            status = await self.rcon_client.command('scoreboard objectives list')
            logger.info(f"スコアボード状態: {status}")
            
        except RconError as e:
            logger.error(f"スコアボード初期化エラー: {e}")
            raise  # 呼び出し元で処理できるようにエラーを再スロー
        except Exception as e:
            logger.error(f"スコアボード初期化中の予期せぬエラー: {e}", exc_info=True)
            raise
        finally:
            if connected_here and await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()
    
    async def update_player_death_counts(self, data_manager: DataManager, manage_connection: bool = True):
        """
        すべてのプレイヤーの死亡回数をスコアボードに反映する
        
        Args:
            data_manager: プレイヤーデータを管理するDataManagerインスタンス
            manage_connection: Trueの場合、このメソッド内でRCONの接続/切断を行う。
                              Falseの場合、呼び出し元で接続/切断を管理する
        """
        connected_here = False
        try:
            if manage_connection and not await self.rcon_client.is_connected():
                await self.rcon_client.connect()
                connected_here = True
            
            # スコアボードが存在することを確認（すでに存在する場合はエラーになるが無視）
            try:
                await self.rcon_client.command('scoreboard objectives add deaths dummy "死亡回数"')
                logger.info("死亡回数スコアボードを作成しました")
            except RconError:
                logger.debug("死亡回数スコアボードはすでに存在します")
            
            # サイドバーに表示
            await self.rcon_client.command('scoreboard objectives setdisplay sidebar deaths')
            
            # 全プレイヤーの死亡回数を取得
            player_stats = data_manager.get_all_stats().get("players", {})
            
            for player_name, stats in player_stats.items():
                death_count = stats.get("death_count", 0)
                cmd = f'scoreboard players set {player_name} deaths {death_count}'
                await self.rcon_client.command(cmd)
                logger.debug(f"プレイヤー {player_name} の死亡回数 {death_count} をスコアボードに設定")
                
            logger.info("すべてのプレイヤーの死亡回数をスコアボードに更新しました")
        except RconError as e:
            logger.error(f"スコアボード更新エラー: {e}")
        except Exception as e:
            logger.error(f"スコアボード更新中の予期せぬエラー: {e}", exc_info=True)
        finally:
            if connected_here and await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()
                
    async def update_player_death_count(self, player_name: str, death_count: int, manage_connection: bool = True):
        """
        特定のプレイヤーの死亡回数をスコアボードに反映する
        
        Args:
            player_name: プレイヤー名
            death_count: 設定する死亡回数
            manage_connection: Trueの場合、このメソッド内でRCONの接続/切断を行う。
                              Falseの場合、呼び出し元で接続/切断を管理する
        """
        connected_here = False
        try:
            if manage_connection and not await self.rcon_client.is_connected():
                await self.rcon_client.connect()
                connected_here = True
            
            # スコアボードが存在することを確認（すでに存在する場合はエラーになるが無視）
            try:
                await self.rcon_client.command('scoreboard objectives add deaths dummy "死亡回数"')
                logger.info("死亡回数スコアボードを作成しました")
            except RconError:
                logger.debug("死亡回数スコアボードはすでに存在します")
            
            # サイドバーに表示
            await self.rcon_client.command('scoreboard objectives setdisplay sidebar deaths')
            
            # プレイヤーの死亡回数を設定
            cmd = f'scoreboard players set {player_name} deaths {death_count}'
            await self.rcon_client.command(cmd)
            logger.debug(f"プレイヤー {player_name} の死亡回数 {death_count} をスコアボードに設定")
                
            logger.info(f"プレイヤー {player_name} の死亡回数をスコアボードに更新しました")
        except RconError as e:
            logger.error(f"スコアボード更新エラー: {e}")
        except Exception as e:
            logger.error(f"スコアボード更新中の予期せぬエラー: {e}", exc_info=True)
        finally:
            if connected_here and await self.rcon_client.is_connected():
                await self.rcon_client.disconnect()
