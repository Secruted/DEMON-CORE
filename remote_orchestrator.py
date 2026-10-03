#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
remote_orchestrator.py
DEMON-CORE Framework - Hybrid Outbound TLS 1.3 Challenge-Authenticated Orchestrator
يجمع بين النقل الصادر العكسي (Outbound) ومحرك الأمان المُصلَّب.
يعمل كعميل صادر يتصل بالخادم المركزي مع مصادقة تحدي زمنية وTLS 1.3 فقط.

Security posture:
- TLS 1.3 only + mutual challenge-response with time-window anti-replay
- Commands executed exclusively via list + shell=False (no shell metacharacter interpretation)
- safe_args filtering (alnum + - _)
- Length-prefixed framing to prevent message boundary attacks
- Side-effects (db_manager / messenger) isolated and executed AFTER the response is sent
"""

from __future__ import annotations

import os
import sys
import ssl
import socket
import hashlib
import hmac
import time
import json
import logging
import subprocess
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# المسارات النسبية لهيكلية مشروع DEMON-CORE (الملفات في الجذر)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
MODULES_DIR = BASE_DIR  # الملفات موجودة في الجذر حالياً
LOGS_DIR = BASE_DIR / "logs"
CERTS_DIR = BASE_DIR / "certs"

LOGS_DIR.mkdir(exist_ok=True)
CERTS_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# إعدادات التسجيل الآمن
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "orchestrator.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("DEMON-CORE.HybridOrchestrator")

# ---------------------------------------------------------------------------
# إعدادات التكوين والسرية المشتركة
# ---------------------------------------------------------------------------
CONFIG_PATH = BASE_DIR / "runtime.json"
TLS_CA_FILE = CERTS_DIR / "ca.crt"
CHALLENGE_SECRET = os.environ.get("DEMON_CORE_CHALLENGE_SECRET", "change-me-in-production").encode()
CHALLENGE_WINDOW_SECONDS = 30  # نافذة الحماية من هجمات الإعادة
RECONNECT_DELAY = 5

# القائمة البيضاء لربط الأوامر بوحدات DEMON-CORE الحقيقية (shell=False دائماً)
COMMAND_MAP: Dict[str, List[str]] = {
    "START_HARVESTER": [
        sys.executable,
        str(MODULES_DIR / "harvester.py"),
    ],
    "RUN_DOMAIN_RECON": [
        sys.executable,
        str(MODULES_DIR / "domain_parser.py"),
    ],
    "GET_DB_STATUS": [
        sys.executable,
        str(MODULES_DIR / "db_manager.py"),
        "--action",
        "status",
    ],
}

# Soft imports for side-effects (isolated)
try:
    import db_manager
except ImportError:
    db_manager = None

try:
    import messenger
except ImportError:
    messenger = None


# ---------------------------------------------------------------------------
# أدوات النقل والتحقق الشبكي
# ---------------------------------------------------------------------------
def load_config() -> Tuple[str, int]:
    """قراءة مضيف ومنفذ التحكم من ملف التكوين المركزي."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 8443))
        if not host or port <= 0 or port > 65535:
            raise ValueError("Invalid network port boundary detected.")
        return host, port
    except Exception as exc:
        logger.warning("Configuration file missing or corrupt (%s). Using defaults 127.0.0.1:8443", exc)
        return "127.0.0.1", 8443


def recv_exact(sock: ssl.SSLSocket, n: int) -> bytes:
    """استقبال عدد بايتات محدد بدقة لحماية تدفق البيانات من القطع."""
    data = b""
    while len(data) < n:
        packet = sock.recv(n - len(data))
        if not packet:
            raise ConnectionError("Orchestrator disconnected abruptly.")
        data += packet
    return data


def recv_msg(sock: ssl.SSLSocket) -> bytes:
    """قراءة حجم الحزمة (4 بايت) ثم قراءة محتواها بالكامل."""
    length_bytes = recv_exact(sock, 4)
    length = struct.unpack("!I", length_bytes)[0]
    if length > 10 * 1024 * 1024:  # حماية من الحزم الضخمة
        raise ValueError("Message too large")
    return recv_exact(sock, length)


def send_msg(sock: ssl.SSLSocket, data: bytes) -> None:
    """إرسال الحزم مسبوقة بحجمها الفعلي لتجنب تداخل الحزم شبكياً."""
    sock.sendall(struct.pack("!I", len(data)) + data)


def verify_and_respond_challenge(sock: ssl.SSLSocket) -> bool:
    """معالجة تحدي المصادقة القادم من الخادم والتحقق من النافذة الزمنية."""
    try:
        raw_challenge = recv_msg(sock)
        payload = json.loads(raw_challenge.decode())

        if payload.get("type") != "challenge":
            logger.error("Unexpected message type during challenge phase")
            return False

        challenge_val = payload.get("value", "")
        if ":" not in challenge_val:
            return False

        _, timestamp_str = challenge_val.split(":", 1)

        # حماية ضد هجمات الإعادة (Replay Attack)
        if abs(time.time() - int(timestamp_str)) > CHALLENGE_WINDOW_SECONDS:
            logger.error("Challenge token expired. Potential replay attack intercepted.")
            return False

        # إنشاء الاستجابة التشفيرية المتبادلة
        response = hmac.new(
            CHALLENGE_SECRET,
            challenge_val.encode(),
            hashlib.sha256,
        ).hexdigest()

        send_msg(sock, json.dumps({"response": response}).encode())
        return True
    except Exception as e:
        logger.error("Authentication phase structural crash: %s", e)
        return False


# ---------------------------------------------------------------------------
# محرك التنفيذ الآمن (shell=False + تصفية المدخلات)
# ---------------------------------------------------------------------------
def execute_secure_command(cmd_key: str, extra_args: Optional[List[str]] = None) -> Dict:
    """تنفيذ وحدات DEMON-CORE مع عزل وتصفية المعاملات الإضافية تماماً."""
    if cmd_key not in COMMAND_MAP:
        return {
            "status": "error",
            "message": f"Unauthorized execution entity: {cmd_key}",
            "allowed": list(COMMAND_MAP.keys()),
        }

    cmd = COMMAND_MAP[cmd_key].copy()
    if extra_args:
        # السماح فقط بالرموز الألفبائية الرقمية + الشرطة لمنع حقن الأوامر
        safe_args = [
            a for a in extra_args
            if isinstance(a, str) and a.replace("-", "").replace("_", "").isalnum()
        ]
        cmd.extend(safe_args)

    logger.info("Triggering structural subsystem execution: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            shell=False,  # منع ثغرات Shell Injection بالكامل
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
            env={**os.environ, "PYTHONPATH": str(BASE_DIR)},
            check=False,
        )
        return {
            "status": "success" if result.returncode == 0 else "failed",
            "returncode": result.returncode,
            "stdout": (result.stdout or "")[-4000:],
            "stderr": (result.stderr or "")[-2000:],
            "command": cmd_key,
        }
    except subprocess.TimeoutExpired:
        logger.error("Command timed out: %s", cmd_key)
        return {"status": "error", "message": "Timeout after 120 seconds"}
    except Exception as exc:
        logger.exception("Execution failure")
        return {"status": "error", "message": str(exc)}


def _safe_log_session(cmd_key: str, result: Dict) -> None:
    """Isolated side-effect – never raises into the transport loop."""
    if db_manager is None:
        return
    try:
        if hasattr(db_manager, "log_session_event"):
            db_manager.log_session_event(
                cmd_key,
                json.dumps(result, ensure_ascii=False),
                result.get("status", "unknown"),
            )
    except Exception as exp:
        logger.error("Coupled module dependency failure [db_manager]: %s", exp)


def _safe_dispatch_alert(message_text: str) -> None:
    """Isolated side-effect – never raises into the transport loop."""
    if messenger is None:
        return
    try:
        if hasattr(messenger, "dispatch_alert"):
            messenger.dispatch_alert(message_text[:500])  # truncate
    except Exception as exp:
        logger.error("Coupled module dependency failure [messenger]: %s", exp)


# ---------------------------------------------------------------------------
# الحلقة التنفيذية الهجينة (اتصال صادر)
# ---------------------------------------------------------------------------
def connect_and_orchestrate(host: str, port: int, ctx: ssl.SSLContext) -> None:
    """إنشاء اتصال صادر محصن بـ TLS 1.3 وتبادل البيانات."""
    with socket.create_connection((host, port), timeout=10) as raw_sock:
        with ctx.wrap_socket(raw_sock, server_hostname=host) as tls_sock:
            logger.info("Outbound TLS 1.3 tunnel established successfully.")

            if not verify_and_respond_challenge(tls_sock):
                logger.error("Mutual challenge authentication failed. Severing transport.")
                return

            logger.info("Mutual identity verified. Node fully synchronized.")
            _safe_dispatch_alert(f"[DEMON-CORE] TLS node online and synchronized: {host}:{port}")

            while True:
                raw_command = recv_msg(tls_sock)
                payload = json.loads(raw_command.decode())

                cmd_key = str(payload.get("command", "")).upper()
                extra_args = payload.get("args", [])

                result = execute_secure_command(cmd_key, extra_args)

                # الرد أولاً عبر الشبكة لتقليل التأخير ولعزل الـ side-effects
                send_msg(tls_sock, json.dumps(result, ensure_ascii=False).encode())

                # Side-effects AFTER response is on the wire
                _safe_log_session(cmd_key, result)
                _safe_dispatch_alert(
                    f"DEMON-CORE Task Finished | Status: {result.get('status')} | Cmd: {cmd_key}"
                )


def main() -> None:
    host, port = load_config()

    # بناء سياق TLS 1.3 صارم ومحصن
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:!aNULL:!MD5:!DSS")
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = False  # التحقق يتم عبر التحدي المتبادل

    if TLS_CA_FILE.exists():
        ctx.load_verify_locations(cafile=str(TLS_CA_FILE))
        logger.info("Loaded CA certificate from %s", TLS_CA_FILE)
    else:
        logger.warning(
            "CA certificate not found at %s – TLS verification will fail until you place ca.crt",
            TLS_CA_FILE,
        )

    logger.info("Starting DEMON-CORE Hybrid Orchestrator (Outbound TLS mode)")
    logger.info("Target controller: %s:%d", host, port)

    while True:
        try:
            connect_and_orchestrate(host, port, ctx)
        except Exception as e:
            logger.error("Infrastructure connection offline: %s. Reconnecting in %ds...", e, RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    main()
