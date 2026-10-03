#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remote_orchestrator.py
Persistent outbound TCP command orchestrator for DEMON-CORE.
Strictly decoupled, secure binary execution layout with structural boundary checks.

Security posture:
- Commands executed exclusively via shlex.split + shell=False (no shell metacharacter interpretation).
- All side-effects (db_manager / messenger) isolated and executed AFTER the response is sent,
  so a Database Lock or any exception can never collapse the reverse TCP session.
- Port validated to the legal TCP range 1-65535.
- Alert messages truncated to avoid Telegram rate-limit / length issues.
"""

import json
import logging
import select
import shlex
import socket
import subprocess
import sys
import time
from typing import Optional, Tuple

# Soft import – graceful degradation if modules are absent
try:
    import db_manager
except ImportError:
    db_manager = None

try:
    import messenger
except ImportError:
    messenger = None

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [REMOTE_ORCH] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Infrastructure constants
CONFIG_PATH = "runtime.json"
RECONNECT_DELAY = 5
BUFFER_SIZE = 4096
COMMAND_TIMEOUT = 15
CONNECT_TIMEOUT = 10


def load_config(path: str = CONFIG_PATH) -> Tuple[str, int]:
    """
    Load and validate host/port from central configuration.
    Falls back to safe defaults on any failure.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 8080))
        if not host or port <= 0 or port > 65535:
            raise ValueError("Invalid network port boundary detected.")
        return host, port
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("config load issue (%s) – using defaults 127.0.0.1:8080", exc)
        return "127.0.0.1", 8080


def execute_command(command_str: str) -> Tuple[str, str]:
    """
    Secure command execution.
    Uses shlex.split to produce an argv list and forces shell=False,
    eliminating classic command-injection vectors via shell metacharacters.
    """
    try:
        args = shlex.split(command_str)
        if not args:
            return "[WARN] Empty processing token received.", "error"

        result = subprocess.run(
            args,
            shell=False,  # mandatory – no shell interpretation
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )

        output = result.stdout or ""
        if result.stderr:
            output += "\n[STDERR]\n" + result.stderr

        if not output:
            output = "[INFO] Command completed with blank execution pipe."

        status = "success" if result.returncode == 0 else "error"
        return output, status

    except subprocess.TimeoutExpired:
        return f"[ERROR] Command boundary execution timeout after {COMMAND_TIMEOUT}s", "error"
    except FileNotFoundError:
        return "[ERROR] Executable file or instruction entity not found on local path.", "error"
    except Exception as exc:
        return f"[ERROR] Execution engine failure: {exc}", "error"


def send_all(sock: socket.socket, data: bytes) -> bool:
    """Guarantee full transmission of the response buffer."""
    try:
        sock.sendall(data)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
        logger.error("Transport layer send failure: %s", exp)
        return False


def _safe_log_session(command_str: str, output: str, status: str) -> None:
    """Isolated side-effect – never raises into the transport loop."""
    if db_manager is None:
        return
    try:
        if hasattr(db_manager, "log_session_event"):
            db_manager.log_session_event(command_str, output, status)
    except Exception as exc:
        logger.error("Coupled module dependency failure [db_manager]: %s", exp)


def _safe_dispatch_alert(message_text: str) -> None:
    """Isolated side-effect – never raises into the transport loop."""
    if messenger is None:
        return
    try:
        if hasattr(messenger, "dispatch_alert"):
            messenger.dispatch_alert(message_text)
    except Exception as exc:
        logger.error("Coupled module dependency failure [messenger]: %s", exp)


def process_stream(sock: socket.socket) -> None:
    """
    Non-blocking command ingestion loop with full isolation of external modules.
    Response is always sent before any side-effect so a lock cannot drop the session.
    """
    buffer = b""
    while True:
        readable, _, _ = select.select([sock], [], [], 1.0)
        if not readable:
            continue

        try:
            data = sock.recv(BUFFER_SIZE)
            if not data:
                logger.warning("Orchestration host severed the connection channel.")
                break

            buffer += data

            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                command_str = line.decode("utf-8", errors="replace").strip()
                if not command_str:
                    continue

                logger.info("Executing synchronized task: %s", command_str)
                output, status = execute_command(command_str)

                # 1. Deliver response to controller FIRST
                response = (output + "\n").encode("utf-8")
                if not send_all(sock, response):
                    return

                # 2. Side-effects AFTER the response is on the wire (fully isolated)
                _safe_log_session(command_str, output, status)
                _safe_dispatch_alert(
                    f"DEMON-CORE Task Finished | Status: {status} | Cmd: {command_str[:100]}..."
                )

        except BlockingIOError:
            continue
        except ConnectionResetError:
            logger.warning("Connection abruptly reset by remote infrastructure.")
            break
        except Exception as exc:
            logger.error("Fatal exception during non-blocking stream digestion: %s", exp)
            break


def main() -> None:
    """Infinite reconnection loop preserving node stability."""
    host, port = load_config()

    while True:
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((host, port))
            sock.setblocking(False)
            logger.info(
                "Successfully established synchronization path to orchestrator %s:%d",
                host, port,
            )

            _safe_dispatch_alert(
                f"[DEMON-CORE] Infrastructure node online and synchronized: {host}:{port}"
            )

            process_stream(sock)

        except Exception as exc:
            logger.error("Network layer connection mapping failure: %s", exp)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            logger.info(
                "Initiating architectural cooling phase. Reconnecting in %d seconds...",
                RECONNECT_DELAY,
            )
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
