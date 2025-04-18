import re
import logging
from typing import Optional, Tuple, Dict
from openai import AsyncOpenAI, OpenAIError as OpenAI_API_Error # Rename to avoid clash

# Import custom exception
from ..core.exceptions import OpenAIError

logger = logging.getLogger(__name__)

class DeathAnalyzer:
    """Analyzes death messages, potentially using OpenAI for descriptions."""

    # Regex to capture common Minecraft death messages (Java Edition)
    # Adjusted to be slightly more general and capture the core message part
    DEATH_MESSAGE_REGEX = re.compile(
        r"\[\d{2}:\d{2}:\d{2}\] \[Server thread/INFO\]: (\w+)\s+(.+)"
        # Example: PlayerName was slain by Zombie
        # Example: PlayerName fell from a high place
        # Group 1: Player Name, Group 2: Rest of the message
    )

    # Keywords indicating a death message (add more as needed)
    DEATH_KEYWORDS = [
        "was slain by", "fell from", "drowned", "tried to swim in lava",
        "went up in flames", "blew up", "was killed by", "starved to death",
        "suffocated in a wall", "fell out of the world", "experienced kinetic energy",
        "discovered the floor was lava", "hit the ground too hard", "froze to death",
        "was pricked to death", "was shot by", "was fireballed by", "was pummeled by",
        "died", "withered away"
    ]


    def __init__(self, api_key: Optional[str], base_url: Optional[str], model: Optional[str]):
        self.openai_model = model
        self.openai_client: Optional[AsyncOpenAI] = None
        if api_key and base_url and model:
            try:
                self.openai_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
                logger.info(f"OpenAI client initialized for model: {model}")
            except Exception as e:
                 logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
                 # Continue without OpenAI functionality
                 self.openai_client = None
        else:
            logger.warning("OpenAI API key, URL, or model not configured. AI description generation disabled.")


    def parse_death_message(self, log_line: str) -> Optional[Tuple[str, str]]:
        """
        Parses a log line to find a player death message based on keywords.

        Args:
            log_line: The log line string.

        Returns:
            A tuple containing (player_name, raw_death_message) if a death message is found,
            otherwise None.
        """
        match = self.DEATH_MESSAGE_REGEX.search(log_line)
        if not match:
            return None

        player_name = match.group(1)
        message_part = match.group(2)

        # Check if the message part contains any death keywords
        if any(keyword in message_part for keyword in self.DEATH_KEYWORDS):
            # Reconstruct the core death message (player + message part)
            raw_death_message = f"{player_name} {message_part}"
            logger.debug(f"Parsed death message: Player='{player_name}', Message='{raw_death_message}'")
            return player_name, raw_death_message
        else:
            # Matched the general pattern but not a death keyword
            return None


    async def analyze_death_cause(self, raw_death_message: str) -> Dict[str, str]:
        """
        Analyzes a death message and generates both a short summary and a detailed description.

        Args:
            raw_death_message: The raw death message extracted from the log.

        Returns:
            A dictionary containing 'summary' (short cause) and 'description' (detailed explanation)
        """
        # Extract player name from the raw message itself for the prompt
        if raw_death_message is None:
            player_name = "プレイヤー"
        else:
            player_name_match = re.match(r"(\w+)", raw_death_message)
            player_name = player_name_match.group(1) if player_name_match else "プレイヤー"

        # Default values if AI is unavailable
        result = {
            "summary": "死亡",  # Default generic summary
            "description": f"死因: `{raw_death_message}`"  # Raw message as fallback
        }

        if not self.openai_client or not self.openai_model:
            logger.warning("OpenAI client/model not available. Skipping AI analysis.")
            result["description"] += "\n\n_(AIによる説明生成は設定されていません)_"
            return result

        try:
            prompt = f"""
            Minecraftのプレイヤー「{player_name}」が死にました。
            サーバーログの死因メッセージは「{raw_death_message}」です。
            
            以下の2つの情報を生成してください：
            
            1. 死因の短い要約（5文字〜10文字程度）：例「ゾンビに食い殺された」「クリーパーに爆破された」「落下死」など
            2. 詳細な状況説明（200文字以内）：何が起きたのか状況を想像し、簡潔かつ少しユーモラスかつ辛辣に説明
            
            必ず以下のフォーマットで回答してください：
            
            要約: [短い死因]
            説明: [詳細な状況説明]
            """
            
            logger.debug(f"Sending prompt to OpenAI for {player_name}: {prompt}")
            response = await self.openai_client.chat.completions.create(
                model=self.openai_model,
                messages=[
                    {"role": "system", "content": "あなたはMinecraftのイベントを解説する面白いナレーターです。"},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=200,
                temperature=0.7,
            )
            generated_text = response.choices[0].message.content
            if generated_text is not None:
                generated_text = generated_text.strip()

            if generated_text:
                # Parse the response to extract summary and description
                summary_match = re.search(r"要約:\s*(.+?)(?:\n|$)", generated_text)
                description_match = re.search(r"説明:\s*(.+?)(?:\n|$)", generated_text, re.DOTALL)
                
                if summary_match:
                    result["summary"] = summary_match.group(1).strip()
                
                if description_match:
                    result["description"] = description_match.group(1).strip()
                else:
                    # If parsing fails, use whole response as description
                    result["description"] = generated_text
                
                logger.info(f"Generated death analysis using OpenAI for {player_name}")
            else:
                logger.warning(f"OpenAI returned empty response for {player_name}")
                result["description"] += "\n\n_(AIが説明を生成できませんでした)_"

        except OpenAI_API_Error as e:
            # Catch specific API errors from the library
            logger.error(f"OpenAI API error generating death analysis for {player_name}: {e}", exc_info=True)
            error_code = getattr(e, 'code', 'unknown')
            result["description"] += f"\n\n_(AIによる説明生成中にAPIエラーが発生しました: {error_code})_"
            # Raise custom exception for handling upstream
            raise OpenAIError(f"OpenAI API error: {e}") from e
        except Exception as e:
            # Catch other potential errors (network issues, etc.)
            logger.error(f"Unexpected error generating death analysis using OpenAI for {player_name}: {e}", exc_info=True)
            result["description"] += "\n\n_(AIによる説明生成中に予期せぬエラーが発生しました)_"
            raise OpenAIError(f"Unexpected error during OpenAI call: {e}") from e

        return result
