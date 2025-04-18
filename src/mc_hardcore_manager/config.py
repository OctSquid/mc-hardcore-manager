import yaml
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, FilePath, DirectoryPath, HttpUrl

class ServerConfig(BaseModel):
    script: FilePath
    ip: str
    port: int
    world_name: str
    world_path: str

class RconConfig(BaseModel):
    port: int
    password: str

class DiscordConfig(BaseModel):
    token: str
    notice_channel_id: int
    admin_channel_id: int
    owner_ids: List[int]

class DataConfig(BaseModel):
    path: FilePath

class OpenAIConfig(BaseModel):
    url: HttpUrl
    api_key: str
    model: str

class DeathExplosionConfig(BaseModel):
    enabled: bool = False
    delay: int = Field(default=0, ge=0)

class DeathTitleConfig(BaseModel):
    enabled: bool = True
    fade_in: int = Field(default=10, ge=0)
    stay: int = Field(default=70, ge=0)
    fade_out: int = Field(default=20, ge=0)

class DeathSoundConfig(BaseModel):
    enabled: bool = True
    sound_id: str = "minecraft:entity.wither.death"
    volume: float = Field(default=1.0, ge=0.0)
    pitch: float = Field(default=0.7, ge=0.0)

class Config(BaseModel):
    server: ServerConfig
    rcon: RconConfig
    discord: DiscordConfig
    data: DataConfig
    openai: OpenAIConfig = Field(alias="openAI")
    death_explosion: DeathExplosionConfig
    death_title: DeathTitleConfig = DeathTitleConfig()
    death_sound: DeathSoundConfig = DeathSoundConfig()

_config: Optional[Config] = None

def load_config(path: str = "config.yaml") -> Config:
    """Loads and validates the configuration from a YAML file."""
    global _config
    if _config is None:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            _config = Config(**config_data)
        except FileNotFoundError:
            # Consider logging this error
            print(f"Error: Configuration file not found at {path}")
            raise
        except yaml.YAMLError as e:
            print(f"Error parsing configuration file: {e}")
            raise
        except Exception as e: # Catch Pydantic validation errors etc.
            print(f"Error loading or validating configuration: {e}")
            raise
    return _config

def get_config() -> Config:
    """Returns the loaded configuration."""
    if _config is None:
        raise RuntimeError("Configuration has not been loaded. Call load_config() first.")
    return _config

# Example usage (optional, can be removed or placed under if __name__ == "__main__":)
# if __name__ == "__main__":
#     try:
#         config = load_config()
#         print("Configuration loaded successfully:")
#         print(config.model_dump_json(indent=2))
#     except Exception as e:
#         print(f"Failed to load configuration: {e}")
