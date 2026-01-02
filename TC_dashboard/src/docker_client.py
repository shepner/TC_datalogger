"""Docker client for checking container status."""

import logging
import json
import http.client
import urllib.parse
from typing import Dict, Optional

try:
    import docker
except ImportError:
    docker = None

logger = logging.getLogger(__name__)


class DockerClient:
    """Client for interacting with Docker API."""

    def __init__(self):
        """Initialize Docker client."""
        self.client = None
        self.use_http = False
        self.socket_path = '/var/run/docker.sock'
        
        import os
        if not os.path.exists(self.socket_path):
            logger.warning("Docker socket not found, Docker features unavailable")
            return
        
        # Try Docker SDK first
        if docker:
            try:
                # Use the socket path directly, not with unix:// prefix
                self.client = docker.DockerClient(base_url='unix://' + self.socket_path)
                self.client.ping()
                logger.info("Successfully connected to Docker via SDK")
            except Exception as e:
                logger.warning(f"Docker SDK failed: {e}, falling back to HTTP")
                self.client = None
                self.use_http = True
        else:
            logger.warning("Docker SDK not available, using HTTP fallback")
            self.use_http = True

    def _docker_http_request(self, method: str, path: str, body: bytes = None) -> Dict:
        """Make HTTP request to Docker socket."""
        try:
            import socket
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(self.socket_path)
            
            # Build HTTP request
            headers = f"Host: localhost\r\nConnection: close\r\n"
            if body:
                headers += f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            request = f"{method} {path} HTTP/1.1\r\n{headers}\r\n"
            sock.sendall(request.encode())
            if body:
                sock.sendall(body)
            
            # Read response
            response = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            sock.close()
            
            # Parse HTTP response
            response_str = response.decode('utf-8', errors='ignore')
            
            # Find the HTTP status line
            first_line_end = response_str.find('\r\n')
            if first_line_end == -1:
                raise Exception("Invalid HTTP response")
            
            status_line = response_str[:first_line_end]
            parts = status_line.split()
            if len(parts) < 2:
                raise Exception(f"Invalid status line: {status_line}")
            
            status_code = int(parts[1])
            
            if status_code == 404:
                raise Exception("404 No such container")
            # 201 Created is valid for POST requests (like creating exec instances)
            if status_code not in (200, 201):
                raise Exception(f"HTTP {status_code}: {status_line}")
            
            # Find headers end (double CRLF) - use bytes for accurate position
            header_end = response.find(b'\r\n\r\n')
            if header_end == -1:
                raise Exception("No body in response")
            
            # Get raw body bytes (after headers)
            body_raw = response[header_end + 4:]
            
            # For non-JSON responses (like /_ping), return empty dict
            if path == '/_ping':
                return {}
            
            # Check if chunked transfer encoding
            headers = response_str[:header_end]
            is_chunked = 'Transfer-Encoding: chunked' in headers or 'transfer-encoding: chunked' in headers
            
            if is_chunked:
                # Decode chunked encoding first
                body_bytes = self._decode_chunked(body_raw)
            else:
                body_bytes = body_raw
            
            # Find first { and last } in the decoded body
            first_brace = body_bytes.find(b'{')
            last_brace = body_bytes.rfind(b'}')
            
            if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
                raise Exception("No valid JSON object found in response")
            
            # Extract and decode JSON
            json_bytes = body_bytes[first_brace:last_brace + 1]
            json_str = json_bytes.decode('utf-8', errors='ignore').strip()
            
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}, preview: {json_str[:300]}")
                raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            raise Exception(f"Invalid JSON response: {e}")
        except Exception as e:
            logger.error(f"HTTP request to Docker socket failed: {e}")
            raise

    def _decode_chunked(self, data: bytes) -> bytes:
        """Decode HTTP chunked transfer encoding."""
        result = b''
        pos = 0
        
        while pos < len(data):
            # Find chunk size line
            line_end = data.find(b'\r\n', pos)
            if line_end == -1:
                break
            
            chunk_size_line = data[pos:line_end].decode('utf-8', errors='ignore').strip()
            if not chunk_size_line:
                break
            
            # Chunk size is in hex, may have extensions after semicolon
            chunk_size_str = chunk_size_line.split(';')[0].strip()
            
            try:
                chunk_size = int(chunk_size_str, 16)
            except ValueError:
                # Not a valid chunk size, might be end of chunks
                break
            
            if chunk_size == 0:
                # Last chunk
                break
            
            # Move past chunk size line and CRLF
            pos = line_end + 2
            
            # Ensure we have enough data
            if pos + chunk_size > len(data):
                # Incomplete chunk, take what we have
                chunk_data = data[pos:]
                result += chunk_data
                break
            
            # Read chunk data
            chunk_data = data[pos:pos + chunk_size]
            result += chunk_data
            
            # Move past chunk data and trailing CRLF
            pos += chunk_size + 2
            
            # Check for end of chunks (0\r\n\r\n)
            if pos < len(data) and data[pos:pos+5] == b'0\r\n\r\n':
                break
        
        return result

    def is_available(self) -> bool:
        """Check if Docker client is available."""
        if self.use_http:
            try:
                # Test connection with ping (returns plain text "OK", not JSON)
                import socket
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2)
                sock.connect(self.socket_path)
                sock.sendall(b'GET /_ping HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
                response = sock.recv(1024)
                sock.close()
                # Just check if we got a 200 response
                if b'200 OK' in response:
                    return True
                return False
            except Exception as e:
                logger.debug(f"Ping failed: {e}")
                return False
        return self.client is not None

    def get_container_status(self, container_name: str) -> Dict[str, any]:
        """
        Get status of a Docker container.

        Args:
            container_name: Name of the container

        Returns:
            Dictionary with status information:
            - running: bool
            - status: str (e.g., "running", "stopped", "not_found")
            - started_at: Optional[str]
        """
        if self.use_http:
            return self._get_container_status_http(container_name)
        
        if not self.client:
            return {
                "running": False,
                "status": "docker_unavailable",
                "started_at": None,
            }

        try:
            container = self.client.containers.get(container_name)
            container.reload()  # Refresh container state

            return {
                "running": container.status == "running",
                "status": container.status,
                "started_at": container.attrs.get("State", {}).get("StartedAt"),
            }
        except docker.errors.NotFound:
            return {
                "running": False,
                "status": "not_found",
                "started_at": None,
            }
        except Exception as e:
            logger.error(f"Error checking container {container_name}: {e}")
            return {
                "running": False,
                "status": "error",
                "started_at": None,
            }

    def _get_container_status_http(self, container_name: str) -> Dict[str, any]:
        """Get container status using HTTP requests to Docker socket."""
        try:
            # URL encode container name
            encoded_name = urllib.parse.quote(container_name, safe='')
            path = f'/containers/{encoded_name}/json'
            
            container_info = self._docker_http_request('GET', path, None)
            state = container_info.get('State', {})
            
            return {
                "running": state.get('Running', False),
                "status": state.get('Status', 'unknown'),
                "started_at": state.get('StartedAt'),
            }
        except Exception as e:
            error_str = str(e)
            if '404' in error_str or 'No such container' in error_str:
                return {
                    "running": False,
                    "status": "not_found",
                    "started_at": None,
                }
            logger.error(f"Error checking container {container_name} via HTTP: {e}")
            return {
                "running": False,
                "status": "error",
                "started_at": None,
            }

    def get_all_containers_status(self, container_names: list) -> Dict[str, Dict]:
        """
        Get status for multiple containers.

        Args:
            container_names: List of container names

        Returns:
            Dictionary mapping container names to their status
        """
        return {
            name: self.get_container_status(name) for name in container_names
        }

    def execute_in_container(self, container_name: str, command: list, timeout: int = 300) -> Dict[str, any]:
        """
        Execute a command in a Docker container.

        Args:
            container_name: Name of the container
            command: Command to execute as a list (e.g., ['python', '-m', 'src.main'])
            timeout: Timeout in seconds (default: 300)

        Returns:
            Dictionary with:
            - success: bool
            - exit_code: int
            - output: str
            - error: str
        """
        if self.use_http:
            return self._execute_in_container_http(container_name, command, timeout)
        
        if not self.client:
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": "Docker client not available",
            }

        try:
            container = self.client.containers.get(container_name)
            exec_result = container.exec_run(
                command,
                detach=False,
                stdout=True,
                stderr=True,
                timeout=timeout
            )
            
            output = exec_result.output.decode('utf-8', errors='ignore') if exec_result.output else ""
            exit_code = exec_result.exit_code
            
            return {
                "success": exit_code == 0,
                "exit_code": exit_code,
                "output": output,
                "error": "" if exit_code == 0 else f"Command exited with code {exit_code}",
            }
        except Exception as e:
            logger.error(f"Error executing command in container {container_name}: {e}")
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": str(e),
            }

    def _execute_in_container_http(self, container_name: str, command: list, timeout: int) -> Dict[str, any]:
        """Execute command in container using HTTP requests to Docker socket."""
        try:
            import json
            import urllib.parse
            
            # URL encode container name
            encoded_name = urllib.parse.quote(container_name, safe='')
            
            # Create exec instance
            exec_config = {
                "AttachStdout": True,
                "AttachStderr": True,
                "Cmd": command,
            }
            
            # POST to create exec instance
            exec_data = self._docker_http_request('POST', f'/containers/{encoded_name}/exec', json.dumps(exec_config).encode('utf-8'))
            exec_id = exec_data.get('Id')
            if not exec_id:
                raise Exception("Failed to create exec instance")
            
            # Start exec instance and read output
            # Docker exec API uses a streaming response with multiplexed stdout/stderr
            import socket
            import struct
            
            start_config = {
                "Detach": False,
                "Tty": False,
            }
            
            # Create socket connection
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(self.socket_path)
            
            # Build POST request to start exec
            exec_id_encoded = urllib.parse.quote(exec_id, safe='')
            start_body = json.dumps(start_config).encode('utf-8')
            request = f"POST /exec/{exec_id_encoded}/start HTTP/1.1\r\n"
            request += f"Host: localhost\r\n"
            request += f"Content-Type: application/json\r\n"
            request += f"Content-Length: {len(start_body)}\r\n"
            request += f"Connection: close\r\n\r\n"
            
            sock.sendall(request.encode())
            sock.sendall(start_body)
            
            # Read response headers
            response_headers = b''
            while b'\r\n\r\n' not in response_headers:
                chunk = sock.recv(1)
                if not chunk:
                    break
                response_headers += chunk
            
            # Read streaming output (Docker uses multiplexed stream format)
            # Format: [8-byte header][payload]
            # Header: [1 byte stream type][3 bytes padding][4 bytes size]
            output = b''
            stdout_data = b''
            stderr_data = b''
            
            try:
                while True:
                    # Read 8-byte header
                    header = sock.recv(8)
                    if len(header) < 8:
                        break
                    
                    stream_type = header[0]  # 1=stdout, 2=stderr
                    size = struct.unpack('>I', header[4:8])[0]
                    
                    if size == 0:
                        continue
                    
                    # Read payload
                    payload = b''
                    while len(payload) < size:
                        chunk = sock.recv(size - len(payload))
                        if not chunk:
                            break
                        payload += chunk
                    
                    if stream_type == 1:  # stdout
                        stdout_data += payload
                    elif stream_type == 2:  # stderr
                        stderr_data += payload
                    
                    # Check for timeout
                    if len(stdout_data) + len(stderr_data) > 10 * 1024 * 1024:  # 10MB limit
                        break
            except socket.timeout:
                pass
            except Exception as e:
                logger.warning(f"Error reading exec output: {e}")
            
            sock.close()
            
            # Combine stdout and stderr
            output = stdout_data + stderr_data
            output_str = output.decode('utf-8', errors='ignore')
            
            # Get exit code by inspecting the exec instance
            # Wait a moment for the exec to complete before checking
            import time
            time.sleep(0.5)
            
            exit_code = -1
            max_retries = 5
            for retry in range(max_retries):
                try:
                    exec_info = self._docker_http_request('GET', f'/exec/{exec_id_encoded}/json', None)
                    exit_code = exec_info.get('ExitCode')
                    # ExitCode can be None if exec hasn't finished yet, or 0/other int if finished
                    if exit_code is not None:
                        break
                    # If still None, wait a bit and retry
                    if retry < max_retries - 1:
                        time.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Error getting exec exit code (retry {retry + 1}/{max_retries}): {e}")
                    if retry < max_retries - 1:
                        time.sleep(0.5)
            
            # If exit_code is still None or -1, check if we got any error output
            if exit_code is None or exit_code == -1:
                # If we have stderr output, assume failure
                if stderr_data and len(stderr_data) > 0:
                    exit_code = 1  # Non-zero indicates failure
                # If we have no output at all, might be an issue
                elif len(output) == 0:
                    exit_code = -1  # Unknown
                else:
                    # If we only have stdout, assume success (exit_code 0)
                    exit_code = 0
            
            return {
                "success": exit_code == 0,
                "exit_code": exit_code,
                "output": output_str,
                "error": "" if exit_code == 0 else f"Command exited with code {exit_code}",
            }
        except Exception as e:
            logger.error(f"Error executing command in container {container_name} via HTTP: {e}")
            return {
                "success": False,
                "exit_code": -1,
                "output": "",
                "error": str(e),
            }

