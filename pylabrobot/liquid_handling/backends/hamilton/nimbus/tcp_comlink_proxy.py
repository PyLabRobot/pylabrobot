import asyncio
import datetime
import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from typing import Dict, Any, List, Optional, Callable
from concurrent.futures import ThreadPoolExecutor

from pylabrobot.io.capture import capturer, get_capture_or_validation_active, Command
from pylabrobot.io.io import IOBase
from pylabrobot.liquid_handling.backends.hamilton.nimbus import COMLINKDLL

import System

logger = logging.getLogger(__name__)


@dataclass
class TcpComLinkProxyCommand(Command):
    """Captures a ComLink method call with basic metadata and TCP traffic."""

    method_name: str
    parameters: Dict[str, Any]
    duration_ms: float
    success: bool
    error: Optional[str]
    timestamp: str
    outgoing_data: bytes
    incoming_data: bytes

    def __init__(
        self,
        device_id: str,
        method_name: str,
        parameters: Dict[str, Any],
        duration_ms: float = 0.0,
        success: bool = True,
        error: Optional[str] = None,
        outgoing_data: bytes = b"",
        incoming_data: bytes = b"",
        module: str = "tcp_comlink_proxy"
    ):
        super().__init__(module=module, device_id=device_id, action=method_name)
        self.method_name = method_name
        self.parameters = parameters
        self.duration_ms = duration_ms
        self.success = success
        self.error = error
        self.timestamp = datetime.datetime.now().isoformat()
        self.outgoing_data = outgoing_data
        self.incoming_data = incoming_data




class SimpleTcpProxy:
    """Simple TCP proxy that captures traffic between client and instrument."""

    def __init__(self, target_host: str, target_port: int, proxy_port: int = 0):
        self.target_host = target_host
        self.target_port = target_port
        self.proxy_port = proxy_port
        self.server_socket = None
        self.is_running = False
        self.on_data_callback: Optional[Callable[[str, bytes], None]] = None

    def start(self) -> bool:
        """Start the proxy server."""
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind(('localhost', self.proxy_port))
            self.server_socket.listen(1)

            self.proxy_port = self.server_socket.getsockname()[1]
            self.is_running = True

            # Start server thread
            server_thread = threading.Thread(target=self._server_loop, daemon=True)
            server_thread.start()

            return True
        except Exception as e:
            logger.error(f"Failed to start TCP proxy: {e}")
            return False

    def stop(self):
        """Stop the proxy server."""
        self.is_running = False
        if self.server_socket:
            self.server_socket.close()
            self.server_socket = None

    def _server_loop(self):
        """Main server loop."""
        while self.is_running:
            try:
                client_socket, _ = self.server_socket.accept()
                threading.Thread(target=self._handle_client, args=(client_socket,), daemon=True).start()
            except Exception as e:
                if self.is_running:
                    logger.error(f"Error accepting connection: {e}")

    def _handle_client(self, client_socket: socket.socket):
        """Handle a client connection."""
        try:
            # Connect to instrument
            instrument_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            instrument_socket.connect((self.target_host, self.target_port))

            # Start bidirectional forwarding with capture
            client_to_instrument = threading.Thread(
                target=self._forward_with_capture,
                args=(client_socket, instrument_socket, "outgoing"),
                daemon=True
            )
            instrument_to_client = threading.Thread(
                target=self._forward_with_capture,
                args=(instrument_socket, client_socket, "incoming"),
                daemon=True
            )

            client_to_instrument.start()
            instrument_to_client.start()

            client_to_instrument.join()
            instrument_to_client.join()

        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
            try:
                instrument_socket.close()
            except:
                pass

    def _forward_with_capture(self, source: socket.socket, destination: socket.socket, direction: str):
        """Forward data and capture it."""
        try:
            while self.is_running:
                data = source.recv(8192)
                if not data:
                    break

                # Capture the data
                if self.on_data_callback:
                    self.on_data_callback(direction, data)

                # Forward to destination
                destination.sendall(data)

        except Exception as e:
            if self.is_running:
                logger.error(f"Error forwarding {direction} data: {e}")
                # Log the error but don't break the loop to allow recovery


class TcpComLinkProxy(IOBase):
    """TCP proxy wrapper for ComLink that captures traffic using Hamilton DLL utilities."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str = None,
    ):
        """Initialize the TcpComLinkProxy.

        Args:
            host: The hostname/IP of the Nimbus instrument
            port: The port of the Nimbus instrument
            client_id: Client ID for ComLink connection (auto-generated if None)
        """
        super().__init__()

        if get_capture_or_validation_active():
            raise RuntimeError("Cannot create a new TcpComLinkProxy while capture or validation is active")

        # Connection details
        self._host = host
        self._port = port
        self.client_id = client_id or str(System.Guid.NewGuid())

        # Unique identifier for logging
        self._unique_id = f"[{self._host}:{self._port}]"

        # Connection state tracking
        self._connection_state = "disconnected"

        # ComLink instance
        self._comlink = None
        self._comlink_type = None

        # Load ComLink type
        self._load_comlink_type()

        # TCP proxy for traffic capture
        self._tcp_proxy = None
        self._proxy_host = "localhost"
        self._proxy_port = None

        # Traffic capture buffers
        self._captured_outgoing = BytesIO()
        self._captured_incoming = BytesIO()

        # Thread pool executor for async operations
        self._executor: Optional[ThreadPoolExecutor] = None

    def _load_comlink_type(self):
        """Load the ComLink type from the DLL."""
        try:
            self._comlink_type = COMLINKDLL.GetType("Hamilton.Components.TransportLayer.ObjectInterfaceCommunication.ComLink")
            logger.debug(f"{self._unique_id} Loaded ComLink type: {self._comlink_type}")
        except Exception as e:
            logger.error(f"{self._unique_id} Failed to load ComLink type: {e}")
            raise

    def _create_comlink_instance(self):
        """Create a new ComLink instance."""
        if self._comlink_type is None:
            raise RuntimeError("ComLink type not loaded")

        try:
            self._comlink = System.Activator.CreateInstance(self._comlink_type)
            logger.debug(f"{self._unique_id} Created ComLink instance")
            return self._comlink
        except Exception as e:
            logger.error(f"{self._unique_id} Failed to create ComLink instance: {e}")
            raise

    async def setup(self):
        """Initialize the ComLink connection through TCP proxy."""
        try:
            logger.info(f"{self._unique_id} Setting up ComLink connection...")

            # Initialize executor (following parent TCP pattern)
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1)

            # Start TCP proxy
            self._tcp_proxy = SimpleTcpProxy(self._host, self._port)
            self._tcp_proxy.on_data_callback = self._on_tcp_data_captured

            if not self._tcp_proxy.start():
                raise RuntimeError("Failed to start TCP proxy")

            # Track proxy details
            self._proxy_port = self._tcp_proxy.proxy_port
            logger.info(f"{self._unique_id} TCP proxy started on {self._proxy_host}:{self._proxy_port}")

            # Create ComLink instance and set it early
            self._comlink = self._create_comlink_instance()

            # Use execute_command for Connect
            await self.execute_command("Connect", self.client_id, self._proxy_host, self._proxy_port)

            # Wait for connection to establish
            await asyncio.sleep(0.5)

            # Use execute_command for GetClientAddress
            client_address = await self.execute_command("GetClientAddress")
            logger.info(f"{self._unique_id} Connected successfully. Client address: {client_address}")
            self._connection_state = "connected"

        except Exception as e:
            logger.error(f"{self._unique_id} Failed to setup ComLink: {e}")
            self._connection_state = "disconnected"
            # Clean up proxy if setup failed
            if self._tcp_proxy:
                self._tcp_proxy.stop()
                self._tcp_proxy = None
            raise

    async def stop(self):
        """Close the ComLink connection and stop TCP proxy."""
        try:
            if self._comlink is not None:
                logger.info(f"{self._unique_id} Closing ComLink connection...")
                # Use execute_command for Close
                await self.execute_command("Close")
                logger.info(f"{self._unique_id} ComLink connection closed")

            if self._tcp_proxy is not None:
                logger.info(f"{self._unique_id} Stopping TCP proxy...")
                self._tcp_proxy.stop()
                logger.info(f"{self._unique_id} TCP proxy stopped")

        except Exception as e:
            logger.error(f"{self._unique_id} Error during cleanup: {e}")
        finally:
            self._connection_state = "disconnected"
            self._comlink = None
            self._tcp_proxy = None
            self._proxy_port = None

            # Clean up executor following parent TCP pattern
            if self._executor is not None:
                self._executor.shutdown(wait=True)
                self._executor = None

    async def write(self, data: bytes, timeout: Optional[float] = None):
        """Write data through ComLink (not implemented - use execute_with_capture instead)."""
        raise NotImplementedError("Direct write not supported. Use execute_with_capture() for ComLink operations.")

    async def read(self, timeout: Optional[int] = None) -> bytes:
        """Read data through ComLink (not implemented - use execute_with_capture instead)."""
        raise NotImplementedError("Direct read not supported. Use execute_with_capture() for ComLink operations.")

    def get_comlink(self):
        """Get the underlying ComLink instance.

        Warning: Direct access to ComLink should be avoided. Use execute_command()
        instead to ensure proper logging and async execution.
        """
        if self._comlink is None:
            raise RuntimeError("ComLink not initialized. Call setup() first.")
        return self._comlink

    async def execute_command(self, method_name: str, *args, **kwargs):
        """Execute a ComLink method with logging asynchronously.

        This is the ONLY way to interact with ComLink to ensure consistent logging.
        """
        if self._comlink is None:
            raise RuntimeError("ComLink not initialized")

        start_time = time.time()

        # Clear previous traffic
        self._captured_outgoing.seek(0)
        self._captured_outgoing.truncate(0)
        self._captured_incoming.seek(0)
        self._captured_incoming.truncate(0)

        def execute_comlink_method():
            """Execute the ComLink method in a separate thread."""
            try:
                method = getattr(self._comlink, method_name)
                return method(*args, **kwargs), True, None
            except Exception as e:
                return None, False, str(e)

        try:
            # Execute ComLink method in thread pool
            loop = asyncio.get_running_loop()
            result, success, error = await loop.run_in_executor(
                self._executor, execute_comlink_method
            )

            if not success:
                raise RuntimeError(error)

        except Exception as e:
            success = False
            error = str(e)
            raise
        finally:
            duration_ms = (time.time() - start_time) * 1000

            # Get captured traffic only if logging
            if get_capture_or_validation_active():
                # Note: Assuming ComLink methods are synchronous and wait for responses,
                # all TCP traffic should be complete by the time we get here
                outgoing_data = self._captured_outgoing.getvalue()
                incoming_data = self._captured_incoming.getvalue()
                # Log command
                self._log_command(method_name, args, kwargs, duration_ms, success, error,
                                  outgoing_data, incoming_data)
            else:
                outgoing_data = incoming_data = b""

        return result

    @property
    def is_connected(self) -> bool:
        """Check if the connection is currently established."""
        return self._connection_state == "connected"

    @property
    def proxy_address(self) -> Optional[str]:
        """Get the proxy address if available."""
        if self._proxy_port is not None:
            return f"{self._proxy_host}:{self._proxy_port}"
        return None

    def _on_tcp_data_captured(self, direction: str, data: bytes):
        """Callback for TCP data captured by the proxy."""
        if get_capture_or_validation_active():  # Only capture when logging
            if direction == "outgoing":
                self._captured_outgoing.write(data)
            else:
                self._captured_incoming.write(data)
            logger.debug(f"{self._unique_id} Captured {direction} data: {len(data)} bytes")

    def _log_command(self, method_name: str, args: tuple, kwargs: dict, duration_ms: float,
                    success: bool = True, error: str = None, outgoing_data: bytes = b"", incoming_data: bytes = b""):
        """Log a command using pylabrobot's capture system."""
        # Prepare parameters - ensure they're JSON serializable
        parameters = {}
        for i, arg in enumerate(args):
            parameters[f"arg_{i}"] = str(arg)
        parameters.update(kwargs)

        # Convert bytes to hex strings for JSON serialization
        outgoing_hex = outgoing_data.hex() if outgoing_data else ""
        incoming_hex = incoming_data.hex() if incoming_data else ""

        # Create command with serializable data
        command = TcpComLinkProxyCommand(
            device_id=self._unique_id,
            method_name=method_name,
            parameters=parameters,
            duration_ms=duration_ms,
            success=success,
            error=error,
            outgoing_data=outgoing_hex,
            incoming_data=incoming_hex
        )

        # Record using pylabrobot's pattern
        capturer.record(command)

    # Convenience methods
    async def get_module_by_name(self, module_name: str):
        """Get a module by name."""
        return await self.execute_command("GetModuleByName", module_name)

    async def get_modules(self):
        """Get all modules."""
        return await self.execute_command("GetModules")

    async def get_client_address(self):
        """Get client address."""
        return await self.execute_command("GetClientAddress")

    def serialize(self) -> dict:
        """Serialize the proxy configuration."""
        return {
            "host": self._host,
            "port": self._port,
            "client_id": self.client_id,
            "proxy_host": self._proxy_host,
            "proxy_port": self._proxy_port,
            "connection_state": self._connection_state,
        }
