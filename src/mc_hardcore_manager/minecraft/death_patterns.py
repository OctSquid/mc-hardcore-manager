"""
Minecraft death message patterns and detection utilities.
This module defines patterns for detecting player death messages in Minecraft server logs.
"""

import re
import logging
from typing import Optional, Tuple, Dict

logger = logging.getLogger(__name__)

# マインクラフトの死亡メッセージのパターン（Java Edition）
# これらのパターンはログメッセージの中からプレイヤーの死亡を検出するために使用されます
DEATH_VERBS = [
    # 基本的な死亡メッセージ
    r'died',  # generic death
    r'was killed',  # /kill command
    
    # プレイヤー/モブによる攻撃
    r'was slain by',  # player/mob attack
    r'was fireballed by',  # fireball attack
    r'was shot by',  # arrows
    r'was pummeled by',  # snowballs/eggs
    r'was killed by .+ using',  # magic attacks
    r'was killed while trying to hurt',  # thorns enchantment
    r'was impaled by',  # trident
    r'was destroyed by',  # mace
    r'was shot by a skull from',  # wither skull
    
    # 環境的な要因による死亡
    r'burned to death',  # fire damage
    r'went up in flames',  # in fire source block
    r'drowned',  # drowning
    r'experienced kinetic energy',  # elytra + wall
    r'blew up',  # explosions
    r'was blown up by',  # explosions by entity
    r'was killed by \[Intentional Game Design\]',  # bed/respawn anchor explosions
    r'hit the ground too hard',  # fall damage < 5 blocks
    r'fell from a high place',  # fall damage > 5 blocks
    r'fell off',  # fall from specific blocks
    r'fell while climbing',  # fall from climbable blocks
    r'was doomed to fall',  # fall death after damage
    r'was impaled on a stalagmite',  # pointed dripstone
    r'tried to swim in lava',  # lava
    r'was struck by lightning',  # lightning
    r'discovered the floor was lava',  # magma block
    r'walked into the danger zone',  # magma block after damage
    r'froze to death',  # powder snow
    r'was frozen to death by',  # powder snow after damage
    r'starved to death',  # hunger
    r'suffocated in a wall',  # inside block
    r'was squished too much',  # entity cramming
    r'was squashed by',  # entity cramming after damage
    r'left the confines of this world',  # world border
    r'fell out of the world',  # void
    r'didn\'t want to live in the same world as',  # void after damage
    r'withered away',  # wither effect
    
    # 特殊な死亡
    r'was pricked to death',  # cactus
    r'walked into a cactus',  # cactus after damage
    r'went off with a bang',  # firework rocket
    r'was squashed by a falling anvil',  # anvil
    r'was squashed by a falling block',  # falling block
    r'was skewered by a falling stalactite',  # falling stalactite
    r'was poked to death by a sweet berry bush',  # sweet berry bush
    r'died from dehydration',  # axolotl/dolphin out of water
    r'was stung to death',  # bee
    r'was obliterated by a sonically-charged shriek',  # warden
    r'didn\'t want to live as',  # killed after damage
]

# ログの日時部分とプレイヤー名、死亡メッセージを検出するパターン
LOG_DATE_PATTERN = r'\[(\d+:\d+:\d+)\]'
PLAYER_NAME_PATTERN = r'([^<>\[\]]+)'  # プレイヤー名に含まれない文字の制約

# 完全な死亡メッセージ検出パターン
# ログの日時部分 + 任意の文字列 + プレイヤー名 + 死亡動詞のいずれか
DEATH_PATTERN = re.compile(
    LOG_DATE_PATTERN + r'.*?: ' + PLAYER_NAME_PATTERN + r' (' + '|'.join(DEATH_VERBS) + r')'
)

def detect_death_message(log_line: str) -> Optional[Dict[str, str]]:
    """
    サーバーログラインからプレイヤーの死亡メッセージを検出します。
    
    Args:
        log_line: サーバーログの1行
        
    Returns:
        死亡メッセージが検出された場合、タイムスタンプ、プレイヤー名、完全な死亡メッセージを含む辞書
        検出されなかった場合はNone
    """
    match = DEATH_PATTERN.search(log_line)
    if not match:
        return None
        
    timestamp = match.group(1)  # HH:MM:SS形式のタイムスタンプ
    
    # プレイヤー名を抽出（先頭と末尾の空白を削除）
    player_name = match.group(2).strip()
    
    # 完全な死亡メッセージを抽出
    full_message = log_line
    
    logger.debug(f"Death detected: Player {player_name} at {timestamp}")
    
    return {
        "timestamp": timestamp,
        "player_name": player_name,
        "full_message": full_message
    }
