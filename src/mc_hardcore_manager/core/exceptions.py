class McHardcoreManagerError(Exception):
    """Base exception class for the application."""
    pass

class ConfigError(McHardcoreManagerError):
    """Exception related to configuration loading or validation."""
    pass

class DataError(McHardcoreManagerError):
    """Exception related to data loading or saving."""
    pass

class RconError(McHardcoreManagerError):
    """Exception related to RCON communication."""
    pass

class ServerProcessError(McHardcoreManagerError):
    """Exception related to Minecraft server process management."""
    pass

class WorldManagementError(McHardcoreManagerError):
    """Exception related to Minecraft world management."""
    pass

class DeathHandlingError(McHardcoreManagerError):
    """Exception related to death handling logic."""
    pass

class OpenAIError(DeathHandlingError):
    """Exception related to OpenAI API interaction."""
    pass
