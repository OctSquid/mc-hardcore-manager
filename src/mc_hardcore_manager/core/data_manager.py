import yaml
import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta

# Assuming exceptions are defined in a sibling module 'exceptions'
from .exceptions import DataError

logger = logging.getLogger(__name__)

DEFAULT_DATA_STRUCTURE = {
    "challenge_count": 0,
    "players": {},
    "current_challenge_start_time": None, # ISO形式の文字列またはNone、現在のワールドの開始時間
    "first_challenge_start_time": None,   # ISO形式の文字列またはNone、最初のチャレンジの開始時間（累計時間計算用）
}

class DataManager:
    """Manages loading, saving, and accessing statistics data."""

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.data = self._load_data()
        # Ensure start time exists if challenge is ongoing (count > 0)
        # This logic might need refinement based on exactly when a challenge "starts"
        if self.data["challenge_count"] > 0 and self.data["current_challenge_start_time"] is None:
             logger.warning("Challenge count > 0 but start time is missing. Setting to now as fallback.")
             self._update_start_time() # Set it to now as a fallback
             self._save_data()


    def _load_data(self) -> Dict[str, Any]:
        """Loads statistics data from the YAML file, performing validation/migration."""
        if not os.path.exists(self.filepath):
            logger.warning(f"Data file not found at {self.filepath}. Creating a new one.")
            new_data = DEFAULT_DATA_STRUCTURE.copy()
            # Don't set start time here; set it when the first challenge *actually* starts (e.g., on first death)
            self._save_data(new_data) # Save the default structure first
            return new_data
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if data is None:
                logger.warning(f"Data file at {self.filepath} is empty. Initializing.")
                return DEFAULT_DATA_STRUCTURE.copy()

            # --- Data Structure Validation/Migration ---
            migrated = False
            if "challenge_attempts" in data and "challenge_count" not in data:
                 data["challenge_count"] = data.pop("challenge_attempts")
                 logger.info("Migrated 'challenge_attempts' to 'challenge_count'.")
                 migrated = True

            if "challenge_count" not in data:
                data["challenge_count"] = 0
                migrated = True
            if "players" not in data:
                data["players"] = {}
                migrated = True
            if "current_challenge_start_time" not in data:
                 # If challenges happened before tracking time, we can't know the exact start
                 data["current_challenge_start_time"] = None if data["challenge_count"] == 0 else "unknown"
                 logger.info("Added 'current_challenge_start_time' field.")
                 migrated = True

            # Validate player data structure
            for player, p_data in data.get("players", {}).items():
                if isinstance(p_data, int): # Old format? (Just death count)
                    data["players"][player] = {"death_count": p_data}
                    logger.info(f"Migrated player data structure for {player}.")
                    migrated = True
                elif isinstance(p_data, dict):
                    if "deaths" in p_data and "death_count" not in p_data:
                        data["players"][player]["death_count"] = p_data.pop("deaths")
                        logger.info(f"Migrated 'deaths' to 'death_count' for player {player}.")
                        migrated = True
                    elif "death_count" not in p_data:
                        data["players"][player]["death_count"] = 0
                        migrated = True
                else:
                    # Handle unexpected player data format
                    logger.warning(f"Unexpected data format for player {player}: {p_data}. Resetting.")
                    data["players"][player] = {"death_count": 0}
                    migrated = True


            if migrated:
                logger.info("Data structure migration applied. Saving updated file.")
                self._save_data(data) # Save immediately after migration

            return data
        except yaml.YAMLError as e:
            logger.error(f"Error parsing data file {self.filepath}: {e}", exc_info=True)
            raise DataError(f"Failed to parse data file: {e}") from e
        except Exception as e:
            logger.error(f"Failed to load data file {self.filepath}: {e}", exc_info=True)
            raise DataError(f"Failed to load data file: {e}") from e

    def _save_data(self, data_to_save: Optional[Dict[str, Any]] = None):
        """Saves the provided data (or current self.data) to the YAML file."""
        data_to_write = data_to_save if data_to_save is not None else self.data
        try:
            dir_path = os.path.dirname(self.filepath)
            if dir_path:
                 os.makedirs(dir_path, exist_ok=True)
            with open(self.filepath, 'w', encoding='utf-8') as f:
                # Use sort_keys=False to maintain order if needed, though dict order isn't guaranteed < 3.7
                yaml.dump(data_to_write, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.debug(f"Data successfully saved to {self.filepath}")
        except Exception as e:
            logger.error(f"Failed to save data to {self.filepath}: {e}", exc_info=True)
            raise DataError(f"Failed to save data: {e}") from e

    def _ensure_player_entry(self, player_name: str):
        """Ensures a player entry exists in the data, initializing if necessary."""
        if player_name not in self.data.get("players", {}):
             if "players" not in self.data: # Should not happen if _load_data works, but safety check
                 self.data["players"] = {}
             self.data["players"][player_name] = {"death_count": 0}
             logger.info(f"Created new data entry for player: {player_name}")

    def _update_start_time(self):
        """
        Updates the challenge start time to the current UTC time in ISO format.
        If this is the first challenge (challenge_count == 1), also sets first_challenge_start_time.
        """
        current_time = datetime.now(timezone.utc).isoformat()
        self.data["current_challenge_start_time"] = current_time
        logger.info(f"Updated challenge start time to: {current_time}")
        
        # 初回のチャレンジの場合は、first_challenge_start_timeも設定
        if self.data["challenge_count"] == 1 and self.data.get("first_challenge_start_time") is None:
            self.data["first_challenge_start_time"] = current_time
            logger.info(f"This is the first challenge. Set first challenge start time to: {current_time}")
        
        # first_challenge_start_timeが存在しない場合（過去のデータから移行した場合など）でも設定
        if self.data.get("first_challenge_start_time") is None and self.data["challenge_count"] > 0:
            self.data["first_challenge_start_time"] = current_time
            logger.info(f"First challenge start time was missing. Set to current time: {current_time}")


    def get_player_death_count(self, player_name: str) -> int:
        """Gets the death count for a specific player."""
        self._ensure_player_entry(player_name)
        return self.data["players"].get(player_name, {}).get("death_count", 0)

    def increment_death_count(self, player_name: str) -> Dict[str, Any]:
        """
        プレイヤーの死亡回数とチャレンジカウントをインクリメントし、
        次のチャレンジの開始時間を更新し、データを保存します。
        更新されたデータ全体を返します。
        """
        # 現在のチャレンジ開始時間（死亡時点でのワールド開始時間）を取得
        current_time = self.data.get("current_challenge_start_time")
        
        self._ensure_player_entry(player_name)
        # プレイヤーの死亡回数を増やす
        self.data["players"][player_name]["death_count"] += 1
        new_death_count = self.data["players"][player_name]["death_count"]
        logger.info(f"プレイヤー {player_name} の死亡回数を {new_death_count} に増やしました")

        # ルールに従い、死亡ごとにチャレンジカウントを増やす
        self.data["challenge_count"] = self.data.get("challenge_count", 0) + 1
        new_attempts_count = self.data["challenge_count"]
        logger.info(f"チャレンジカウントを {new_attempts_count} に増やしました")

        # 死亡後の新しいチャレンジの開始時間を更新
        self._update_start_time()

        self._save_data()
        return self.data # データ全体を返す

    def get_challenge_count(self) -> int:
        """Gets the total number of challenge attempts (count)."""
        return self.data.get("challenge_count", 0)

    def get_start_time(self) -> Optional[str]:
         """Gets the start time of the current challenge as an ISO format string."""
         return self.data.get("current_challenge_start_time")

    def get_elapsed_time_str(self, start_time_iso: Optional[str] = None) -> str:
        """
        チャレンジ開始からの経過時間を計算し、フォーマットして返す。
        start_time_isoが指定されていない場合は、現在のチャレンジ開始時間を使用。
        
        Returns:
            フォーマットされた経過時間文字列
        """
        if start_time_iso is None:
            start_time_iso = self.get_start_time()

        if not start_time_iso or start_time_iso == "unknown":
            return "N/A"
        try:
            # Handle potential timezone info if already present in ISO string
            start_time = datetime.fromisoformat(start_time_iso)
            # If no timezone info, assume UTC as per _update_start_time
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)

            # 日本標準時（UTC+9）を使用して現在時刻を取得
            jst = timezone(timedelta(hours=9))
            now = datetime.now(jst)
            
            # start_timeとnowの間の経過時間を計算
            elapsed = now - start_time
            total_seconds = int(elapsed.total_seconds())
            
            # デバッグ情報の追加
            logger.info(f"挑戦時間計算: 開始時間={start_time.isoformat()}, 現在時間={now.isoformat()}, 経過秒数={total_seconds}")

            if total_seconds < 0:
                logger.warning(f"Calculated negative elapsed time ({total_seconds}s). Start time: {start_time_iso}, Now: {now.isoformat()}")
                return "計算エラー" # Indicate an error

            days, remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)

            parts = []
            if days > 0:
                parts.append(f"{days}日")
            if hours > 0:
                parts.append(f"{hours}時間")
            if minutes > 0:
                parts.append(f"{minutes}分")
            parts.append(f"{seconds}秒")

            return "".join(parts) if parts else "0秒"

        except ValueError:
            logger.error(f"Could not parse start time ISO string: {start_time_iso}")
            return "時間解析エラー"
        except Exception as e:
            logger.error(f"Error calculating elapsed time: {e}", exc_info=True)
            return "経過時間エラー"
    
    def get_first_challenge_start_time(self) -> Optional[str]:
        """最初のチャレンジ開始時間をISO形式の文字列として取得"""
        return self.data.get("first_challenge_start_time")

    def get_total_elapsed_time_str(self) -> str:
        """
        最初のチャレンジ開始から現在までの累計挑戦時間を計算し、フォーマットして返す。
        まだチャレンジが始まっていない場合は "N/A" を返す。
        """
        # チャレンジがまだ始まっていない場合
        if self.get_challenge_count() == 0:
            logger.info("チャレンジカウントが0のため、累計挑戦時間はN/A")
            return "N/A"
            
        # 最初のチャレンジ開始時間を取得
        first_challenge_time_iso = self.get_first_challenge_start_time()
        logger.debug(f"最初のチャレンジ開始時間: {first_challenge_time_iso}")
        
        if not first_challenge_time_iso or first_challenge_time_iso == "unknown":
            # 最初のチャレンジ開始時間が不明の場合
            logger.warning("最初のチャレンジ開始時間が不明です")
            return "計測不能"
            
        try:
            # タイムゾーン情報が含まれている場合も処理
            first_challenge_time = datetime.fromisoformat(first_challenge_time_iso)
            # タイムゾーン情報がない場合はUTCと仮定
            if first_challenge_time.tzinfo is None:
                first_challenge_time = first_challenge_time.replace(tzinfo=timezone.utc)
                logger.debug(f"タイムゾーン情報がないため、UTCを仮定: {first_challenge_time.isoformat()}")
                
            # 現在時刻を取得するが、システムのタイムゾーンを使用
            # UTC時間ではなく、日本時間(Asia/Tokyo)を使うことでローカルでの時間感覚に合わせる
            jst = timezone(timedelta(hours=9))  # 日本標準時（UTC+9）
            now = datetime.now(jst)
            
            # JSTとUTCのタイムゾーン情報を明示的に設定
            jst = timezone(timedelta(hours=9))  # 日本標準時（UTC+9）
            utc = timezone.utc
            
            # 両方のタイムスタンプの詳細情報をログに出力
            logger.debug(f"最初のチャレンジ開始時間（処理前）: {first_challenge_time.isoformat()}, TZ: {first_challenge_time.tzinfo}")
            logger.debug(f"現在時刻（処理前）: {now.isoformat()}, TZ: {now.tzinfo}")
            
            # タイムゾーン情報を再確認、必要に応じて明示的に設定
            if first_challenge_time.tzinfo is None:
                first_challenge_time = first_challenge_time.replace(tzinfo=utc)
                logger.warning(f"開始時間にタイムゾーン情報がなかったためUTCを設定: {first_challenge_time.isoformat()}")
            
            # nowがnoneのケースは考えにくいが念のため
            if now.tzinfo is None:
                now = now.replace(tzinfo=jst)
                logger.warning(f"現在時刻にタイムゾーン情報がなかったためJSTを設定: {now.isoformat()}")
            
            # 開始時間を秒単位でログ出力
            first_challenge_timestamp = first_challenge_time.timestamp()
            now_timestamp = now.timestamp()
            logger.debug(f"開始時間（エポック秒）: {first_challenge_timestamp}")
            logger.debug(f"現在時刻（エポック秒）: {now_timestamp}")
            
            # 累計挑戦時間をエポック秒で計算
            elapsed_seconds = now_timestamp - first_challenge_timestamp
            total_seconds = int(elapsed_seconds)
            
            # 詳細なデバッグログを追加（重要なので警告レベルで出力）
            logger.warning(f"累計時間計算: 開始時間={first_challenge_time.isoformat()}(TZ={first_challenge_time.tzinfo}), "
                        f"現在時間={now.isoformat()}(TZ={now.tzinfo}), "
                        f"エポック秒での差分計算={elapsed_seconds}, "
                        f"経過秒数={total_seconds}")
            
            if total_seconds < 0:
                logger.warning(f"計算した累計挑戦時間が負の値になりました ({total_seconds}秒). "
                               f"最初のチャレンジ開始時間: {first_challenge_time_iso}, 現在時刻: {now.isoformat()}")
                return "計算エラー"
                
            # 時間フォーマット（日、時、分、秒）
            days, remainder = divmod(total_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            parts = []
            if days > 0:
                parts.append(f"{days}日")
            if hours > 0:
                parts.append(f"{hours}時間")
            if minutes > 0:
                parts.append(f"{minutes}分")
            parts.append(f"{seconds}秒")
            
            # 累計挑戦時間が0秒または非常に小さい値の場合は特別なメッセージを返す
            if total_seconds < 5:  # 5秒未満は「開始したばかり」と表示
                logger.info(f"累計挑戦時間が非常に短いです (合計秒数: {total_seconds}). 特別なメッセージを表示します")
                return "開始したばかり"
            else:
                result = "".join(parts) if parts else "0秒"
                logger.info(f"計算した累計挑戦時間: {result} (合計秒数: {total_seconds})")
                return result
            
        except ValueError:
            logger.error(f"最初のチャレンジ開始時間のISO文字列を解析できませんでした: {first_challenge_time_iso}")
            return "時間解析エラー"
        except Exception as e:
            logger.error(f"累計挑戦時間の計算中にエラーが発生しました: {e}", exc_info=True)
            return "経過時間エラー"


    def reset_stats(self) -> Dict[str, Any]:
        """
        チャレンジカウントとプレイヤーの死亡回数をリセットする。
        current_challenge_start_timeとfirst_challenge_start_timeもリセットする。
        新しいチャレンジサイクルを開始するときに使用する。
        """
        self.data["challenge_count"] = 0
        self.data["players"] = {}
        # first_challenge_start_timeとcurrent_challenge_start_timeをリセット
        # これらは次回のワールドリセットまたはサーバー起動時に設定される
        self.data["first_challenge_start_time"] = None
        self.data["current_challenge_start_time"] = None
        
        logger.info("統計とチャレンジ時間をリセットしました。次回のサーバー起動時に新しいチャレンジサイクルが始まります。")
        self._save_data()
        return self.data

    def get_all_stats(self) -> Dict[str, Any]:
         """Returns a copy of the current statistics data."""
         # Return a deep copy if nested dicts might be modified externally
         import copy
         return copy.deepcopy(self.data)


# テスト用コード - 簡単なテスト用にこのままにしておく
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
    TEST_FILE = "test_data_manager_class.yaml"
    # 必要に応じて前回のテストファイルを削除
    # if os.path.exists(TEST_FILE):
    #     os.remove(TEST_FILE)

    print(f"--- DataManagerを初期化中 ({TEST_FILE}) ---")
    try:
        manager = DataManager(TEST_FILE)
        print(f"初期データ: {manager.get_all_stats()}")
        print(f"初期挑戦時間: {manager.get_elapsed_time_str()}")
        print(f"初期累計挑戦時間: {manager.get_total_elapsed_time_str()}")

        print("\n--- 死亡をシミュレート ---")
        stats_after_death1 = manager.increment_death_count("Player1")
        print(f"Player1の死亡後のデータ: {stats_after_death1}")
        start_time_1 = manager.get_start_time()
        first_time = manager.get_first_challenge_start_time()
        print(f"挑戦開始時間 1: {start_time_1}")
        print(f"最初の挑戦開始時間: {first_time}")
        print(f"挑戦時間 1: {manager.get_elapsed_time_str(start_time_1)}") # ほぼ0秒のはず
        print(f"累計挑戦時間 1: {manager.get_total_elapsed_time_str()}") # ほぼ0秒のはず

        import time
        print("\n2秒待機中...")
        time.sleep(2)

        stats_after_death2 = manager.increment_death_count("Player2")
        print(f"\nPlayer2の死亡後のデータ: {stats_after_death2}")
        start_time_2 = manager.get_start_time()
        print(f"挑戦開始時間 2: {start_time_2}") # start_time_1より後のはず
        print(f"最初の挑戦開始時間（変更なし）: {manager.get_first_challenge_start_time()}")
        print(f"挑戦時間 2: {manager.get_elapsed_time_str(start_time_2)}") # ほぼ0秒のはず
        print(f"累計挑戦時間 2: {manager.get_total_elapsed_time_str()}") # 2秒以上のはず

        print(f"\nPlayer1の死亡回数: {manager.get_player_death_count('Player1')}")
        print(f"Player2の死亡回数: {manager.get_player_death_count('Player2')}")
        print(f"チャレンジ回数: {manager.get_challenge_count()}")
        print(f"現在の挑戦時間（最後の死亡からの経過）: {manager.get_elapsed_time_str()}")

        print("\n--- 統計をリセット ---")
        reset_data = manager.reset_stats()
        print(f"リセット後のデータ: {reset_data}")
        print(f"リセット後のチャレンジ回数: {manager.get_challenge_count()}") # 0のはず
        print(f"リセット後のPlayer1の死亡回数: {manager.get_player_death_count('Player1')}") # 0のはず
        # 開始時間は最後の死亡時刻のままのはず（次のチャレンジ開始前の期間の始まり）
        print(f"リセット後の挑戦開始時間: {manager.get_start_time()}")
        print(f"リセット後の最初の挑戦開始時間: {manager.get_first_challenge_start_time()}")
        print(f"リセット後の挑戦時間: {manager.get_elapsed_time_str()}") # 最後の死亡からの時間を継続
        print(f"リセット後の累計挑戦時間: {manager.get_total_elapsed_time_str()}") # 最初のチャレンジからの時間を継続

        print("\n--- リセット後の死亡をシミュレート ---")
        print("1秒待機中...")
        time.sleep(1)
        stats_after_reset_death = manager.increment_death_count("Player3")
        print(f"リセット後のPlayer3の死亡データ: {stats_after_reset_death}")
        start_time_3 = manager.get_start_time()
        print(f"挑戦開始時間 3: {start_time_3}") # start_time_2より後のはず
        print(f"最初の挑戦開始時間（変更なし）: {manager.get_first_challenge_start_time()}")
        print(f"挑戦時間 3: {manager.get_elapsed_time_str(start_time_3)}") # ほぼ0秒のはず
        print(f"累計挑戦時間 3: {manager.get_total_elapsed_time_str()}") # 最初のチャレンジからの時間
        print(f"リセット後の死亡後のチャレンジ回数: {manager.get_challenge_count()}") # 1のはず


    except DataError as e:
         print(f"\nテスト中にDataErrorが発生しました: {e}")
    except Exception as e:
        print(f"\nテスト中に予期せぬエラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 必要に応じてテストファイルをクリーンアップ
        # if os.path.exists(TEST_FILE):
        #     os.remove(TEST_FILE)
        print(f"\nテスト完了。データは {TEST_FILE} に保存されました")
