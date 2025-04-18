import asyncio
import logging
import socket # For catching socket errors
from mcrcon import MCRcon, MCRconException
from typing import Optional

# Import custom exception
from ..core.exceptions import RconError

logger = logging.getLogger(__name__)

class RconClient:
    """A wrapper class for MCRcon to manage connection and command execution."""

    def __init__(self, host: str, port: int, password: str, bot=None):
        self.host = host
        self.port = port
        self.password = password
        # Increase timeout to 30 seconds to prevent connection timeout errors
        self.client = MCRcon(self.host, self.password, port=self.port, timeout=30)
        self._connected = False
        self.bot = bot  # Store reference to the bot instance for death handler access
        logger.info(f"RCON client initialized for {self.host}:{self.port}")

    async def connect(self) -> bool:
        """Establishes a connection to the RCON server."""
        if self._connected:
            logger.debug("RCON connection already established.")
            return True
        try:
            self.client.connect()
            self._connected = True
            logger.info(f"Successfully connected to RCON server at {self.host}:{self.port}")
            return True
        except MCRconException as e:
            logger.error(f"Failed to connect to RCON server at {self.host}:{self.port} (MCRconException): {e}")
            self._connected = False
            # Wrap and raise custom exception
            raise RconError(f"Failed to connect to RCON: {e}") from e
        except socket.error as e: # Catch potential socket errors
             logger.error(f"Socket error during RCON connection to {self.host}:{self.port}: {e}", exc_info=True)
             self._connected = False
             raise RconError(f"Socket error during RCON connection: {e}") from e
        except Exception as e: # Catch other unexpected errors
             logger.error(f"An unexpected error occurred during RCON connection to {self.host}:{self.port}: {e}", exc_info=True)
             self._connected = False
             raise RconError(f"Unexpected error during RCON connection: {e}") from e


    async def disconnect(self):
        """Disconnects from the RCON server."""
        if self._connected:
            try:
                self.client.disconnect()
                self._connected = False
                logger.info(f"Disconnected from RCON server at {self.host}:{self.port}")
            except Exception as e: # MCRcon doesn't seem to raise specific exceptions on disconnect
                logger.error(f"Error during RCON disconnection from {self.host}:{self.port}: {e}", exc_info=True)
                # Assume disconnected even if error occurs during the process
                self._connected = False
                # Optionally raise RconError here too if disconnection failure is critical
                # raise RconError(f"Error during RCON disconnection: {e}") from e
        else:
            logger.debug("RCON client already disconnected.")

    async def command(self, command: str, auto_reconnect: bool = True) -> str:
        """
        Sends a command to the RCON server and returns the response. Raises RconError on failure.
        
        Args:
            command: The Minecraft command to execute
            auto_reconnect: If True, attempt to reconnect if not connected. Default is True.
        """
        # Make this async to potentially allow for non-blocking connect/command later if library supports it
        # For now, it wraps the synchronous library calls.
        if not self._connected:
            if not auto_reconnect:
                raise RconError(f"Not connected to RCON server and auto_reconnect is disabled")
                
            logger.warning("Attempted to send RCON command while not connected. Trying to connect...")
            try:
                await self.connect() # connect now raises RconError on failure
            except RconError as e:
                 logger.error(f"Failed to connect before sending command '{command}': {e}")
                 raise # Re-raise the connection error

        # If connection succeeded or was already established
        try:
            # Consider adding a timeout mechanism here if commands can hang
            response = self.client.command(command)
            logger.debug(f"Sent RCON command: '{command}', Received: '{response}'")
            # Handle cases where the command executes but returns an empty string or error message
            if response is None:
                 logger.warning(f"RCON command '{command}' returned None response.")
                 # Decide if None is an error or valid empty response
                 # return "" # Or raise RconError("Command returned None")
                 return "" # Assume empty string is acceptable for now
            return response
        except MCRconException as e:
            logger.error(f"MCRconException sending command '{command}': {e}")
            self._connected = False # Assume connection lost
            raise RconError(f"MCRcon error sending command '{command}': {e}") from e
        except socket.error as e:
             logger.error(f"Socket error sending RCON command '{command}': {e}", exc_info=True)
             self._connected = False # Assume connection lost
             raise RconError(f"Socket error sending command '{command}': {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error sending RCON command '{command}': {e}", exc_info=True)
            self._connected = False # Assume connection lost
            raise RconError(f"Unexpected error sending command '{command}': {e}") from e

    async def is_connected(self) -> bool:
        """
        Check if the RCON connection is active.
        
        Note: This only checks the internal connection state flag,
        it does not attempt to actually verify the connection with a command.
        For a full connection test, use `test_connection()` instead.
        """
        return self._connected
        
    async def test_connection(self) -> bool:
        """
        Test if the RCON connection is actually working by sending a command.
        
        This is a more thorough check than is_connected() but causes
        network traffic and should be used sparingly.
        """
        if not self._connected:
            return False
        try:
            # Send a simple command to verify connection is working
            await self.command("list", auto_reconnect=False)
            return True
        except Exception as e:
            logger.warning(f"RCON connection test failed: {e}")
            self._connected = False
            return False

    async def close(self):
        """Close the RCON connection (alias for disconnect)."""
        await self.disconnect()

    async def __aenter__(self):
        """Async context manager entry: connect. Returns self."""
        try:
            await self.connect()
        except RconError as e:
             # Log the error but allow the context manager to proceed (or re-raise)
             logger.error(f"RconClient context manager failed to connect: {e}")
             # raise # Uncomment to make connection failure break the 'with' block
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit: disconnect."""
        await self.disconnect()


# Example Usage (for testing) - Needs async context
async def run_tests():
    logging.basicConfig(level=logging.DEBUG)
    # Replace with your actual RCON details for testing
    TEST_HOST = "127.0.0.1"
    TEST_PORT = 25575
    TEST_PASSWORD = "your_rcon_password" # Make sure this matches your server's rcon.password

    logger.info("--- Testing RconClient ---")

    logger.info("Test 1: Connecting and sending 'list' command...")
    rcon = RconClient(TEST_HOST, TEST_PORT, TEST_PASSWORD)
    try:
        await rcon.connect() # Connect explicitly first
        if rcon.is_connected():
            response = await rcon.command("list") # Use await and new method name
            logger.info(f"Test 1 Success: 'list' command response: {response}")
        else:
            # connect() should raise RconError if it fails
            logger.error("Test 1 Failed: connect() did not raise error but is_connected() is False.")
    except RconError as e:
        logger.error(f"Test 1 Failed: RconError occurred: {e}")
    except Exception as e:
        logger.error(f"Test 1 Exception: An unexpected error occurred: {e}", exc_info=True)
    finally:
        await rcon.disconnect() # Disconnect explicitly

    # Test 2: Handling connection failure (e.g., wrong password)
    logger.info("\nTest 2: Testing connection failure (using wrong password)...")
    rcon_fail = RconClient(TEST_HOST, TEST_PORT, "wrong_password")
    try:
        await rcon_fail.connect()
        # If connect() doesn't raise, the test failed
        logger.error("Test 2 Failed: connect() did not raise RconError with wrong password.")
        if rcon_fail.is_connected():
             # Try sending command if somehow connected
             response = await rcon_fail.command("help")
             logger.info(f"Test 2 Response (if connected): {response}")
    except RconError as e:
        logger.info(f"Test 2 Success: Caught expected RconError during connection attempt: {e}")
    except Exception as e:
        logger.error(f"Test 2 Failed: An unexpected error occurred: {e}", exc_info=True)
    finally:
        await rcon_fail.disconnect() # Ensure disconnect is called

    # Test 3: Using context manager (optional, as connect/disconnect are explicit now)
    logger.info("\nTest 3: Testing with async context manager (if implemented)...")
    # Note: The current __enter__/__exit__ are synchronous.
    # For async context manager, you'd need __aenter__/__aexit__.
    # We'll skip testing the sync context manager with async command for now.
    logger.info("Skipping async context manager test as __aenter__/__aexit__ are not implemented.")


    logger.info("\n--- RconClient Testing Complete ---")
    logger.info("Note: For Test 1 to succeed, a Minecraft server must be running")
    logger.info(f"with RCON enabled on {TEST_HOST}:{TEST_PORT} and password '{TEST_PASSWORD}'.")


if __name__ == '__main__':
    # Replace with your actual RCON details for testing
    TEST_HOST = "127.0.0.1"
    TEST_PORT = 25575
    TEST_PASSWORD = "your_rcon_password" # Make sure this matches your server's rcon.password

    asyncio.run(run_tests())
