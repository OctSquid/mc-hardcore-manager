import logging
import asyncio
from typing import Callable, Coroutine, Any, List, Optional

logger = logging.getLogger(__name__)

class DeathEventDispatcher:
    """
    Cogのリスナーの代わりに使用するコールバックベースのシステム。
    プレイヤーの死亡イベントを検出した際に、登録されたすべてのハンドラを呼び出します。
    """
    
    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None):
        """
        DeathEventDispatcher を初期化します。
        
        Args:
            loop: コールバックを実行する asyncio イベントループ
        """
        self.loop = loop or asyncio.get_event_loop()
        # プレイヤー死亡イベント用のハンドラーリスト
        self.death_handlers: List[Callable[[str, str, str], Coroutine[Any, Any, None]]] = []
        logger.info("DeathEventDispatcher initialized")
    
    def register_death_handler(self, handler: Callable[[str, str, str], Coroutine[Any, Any, None]]) -> None:
        """
        プレイヤー死亡イベントのハンドラーを登録します。
        
        Args:
            handler: 非同期コールバック関数。引数は (player_name, death_message, timestamp) です。
        """
        if handler not in self.death_handlers:
            self.death_handlers.append(handler)
            logger.info(f"Death handler registered: {handler.__qualname__}")
        else:
            logger.warning(f"Handler already registered: {handler.__qualname__}")
    
    def unregister_death_handler(self, handler: Callable[[str, str, str], Coroutine[Any, Any, None]]) -> None:
        """
        プレイヤー死亡イベントのハンドラーの登録を解除します。
        
        Args:
            handler: 登録を解除するハンドラー
        """
        if handler in self.death_handlers:
            self.death_handlers.remove(handler)
            logger.info(f"Death handler unregistered: {handler.__qualname__}")
        else:
            logger.warning(f"Attempted to unregister non-existent handler: {handler.__qualname__}")
    
    async def dispatch_death_event(self, player_name: str, death_message: str, timestamp: str) -> None:
        """
        プレイヤー死亡イベントを登録されたすべてのハンドラーにディスパッチします。
        
        Args:
            player_name: 死亡したプレイヤーの名前
            death_message: 死亡メッセージ
            timestamp: イベントのタイムスタンプ
        """
        logger.info(f"Dispatching death event for {player_name}, handlers count: {len(self.death_handlers)}")
        
        for handler in self.death_handlers:
            try:
                logger.info(f"Calling death handler: {handler.__qualname__}")
                await handler(player_name, death_message, timestamp)
                logger.info(f"Handler {handler.__qualname__} completed successfully")
            except Exception as e:
                logger.error(f"Error in death handler {handler.__qualname__}: {e}", exc_info=True)
