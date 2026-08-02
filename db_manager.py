import aiosqlite
import hashlib
import base64
import os
import logging
import time
import asyncio

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.exceptions import InvalidKey

logger = logging.getLogger(__name__)

class DBManager:
    def __init__(self, db_path, password):
        """
        Initializes the encrypted database manager (Async version).
        Connection is established lazily via init_db().
        """
        self.db_path = db_path
        self.password = password
        self.conn = None
        self.cipher_suite = None
        self._lock = asyncio.Lock()

    async def init_db(self):
        """Must be awaited after creating the DBManager instance."""
        self.conn = await aiosqlite.connect(self.db_path)
        await self._initialize_encryption()
        await self._create_tables()
        logger.info("[DB] Asynchronous encrypted vault online.")

    async def _get_or_create_salt(self):
        async with self.conn.execute(
            "CREATE TABLE IF NOT EXISTS _internal_settings (key TEXT PRIMARY KEY, value BLOB)"
        ):
            pass

        async with self.conn.execute(
            "SELECT value FROM _internal_settings WHERE key = 'salt'"
        ) as cursor:
            result = await cursor.fetchone()

        if result:
            salt = result[0]
            logger.info("Retrieved existing encryption salt from DB.")
        else:
            salt = os.urandom(16)
            await self.conn.execute(
                "INSERT INTO _internal_settings (key, value) VALUES ('salt', ?)", (salt,)
            )
            await self.conn.commit()
            logger.info("Generated and stored new encryption salt in DB.")
        return salt

    async def _initialize_encryption(self):
        salt = await self._get_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        try:
            key = base64.urlsafe_b64encode(kdf.derive(self.password.encode()))
            self.cipher_suite = Fernet(key)
        except InvalidKey as e:
            logger.critical(f"Failed to derive encryption key: {e}. Check master password.")
            raise

    def _encrypt(self, data):
        return self.cipher_suite.encrypt(data.encode('utf-8'))

    def _decrypt(self, encrypted_data):
        try:
            return self.cipher_suite.decrypt(encrypted_data).decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            return "[DECRYPTION_ERROR]"

    async def _create_tables(self):
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS assets (asset_hash TEXT PRIMARY KEY, asset_type TEXT, encrypted_value BLOB, source TEXT, timestamp REAL)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS leaks (leak_hash TEXT PRIMARY KEY, leak_type TEXT, encrypted_value BLOB, source TEXT, timestamp REAL)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS pending_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, encrypted_message BLOB, timestamp REAL)"
        )
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS dynamic_queue (url TEXT PRIMARY KEY, origin TEXT, rep INTEGER, birth REAL)"
        )
        await self.conn.commit()

    def _hash_item(self, item_value):
        return hashlib.sha256(item_value.encode('utf-8')).hexdigest()

    async def add_leak(self, leak_type, leak_value, source):
        """Non-blocking write of a harvested leak into the encrypted vault."""
        async with self._lock:
            try:
                leak_hash = self._hash_item(leak_value)
                async with self.conn.execute(
                    "SELECT 1 FROM leaks WHERE leak_hash = ?", (leak_hash,)
                ) as cursor:
                    if await cursor.fetchone():
                        return

                encrypted_value = self._encrypt(leak_value)
                await self.conn.execute(
                    "INSERT INTO leaks (leak_hash, leak_type, encrypted_value, source, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (leak_hash, leak_type, encrypted_value, source, time.time())
                )
                await self.conn.commit()
            except Exception as e:
                logger.error(f"Failed to save leak: {e}")

    async def add_asset(self, asset_type, asset_value, source):
        async with self._lock:
            try:
                asset_hash = self._hash_item(asset_value)
                async with self.conn.execute(
                    "SELECT 1 FROM assets WHERE asset_hash = ?", (asset_hash,)
                ) as cursor:
                    if await cursor.fetchone():
                        return

                encrypted_value = self._encrypt(asset_value)
                await self.conn.execute(
                    "INSERT INTO assets (asset_hash, asset_type, encrypted_value, source, timestamp) VALUES (?, ?, ?, ?, ?)",
                    (asset_hash, asset_type, encrypted_value, source, time.time())
                )
                await self.conn.commit()
            except Exception as e:
                logger.error(f"Failed to save asset: {e}")

    async def save_lead(self, url, origin):
        async with self._lock:
            try:
                await self.conn.execute(
                    "INSERT OR REPLACE INTO dynamic_queue (url, origin, rep, birth) VALUES (?, ?, ?, ?)",
                    (url, origin, 100, time.time())
                )
                await self.conn.commit()
            except Exception as e:
                logger.error(f"Failed to save lead {url}: {e}")

    async def load_dynamic_queue(self):
        try:
            async with self.conn.execute("SELECT url, rep, birth FROM dynamic_queue") as cursor:
                rows = await cursor.fetchall()
                return {row[0]: {'rep': row[1], 'birth': row[2], 'fails': 0} for row in rows}
        except Exception as e:
            logger.error(f"Failed to load dynamic queue: {e}")
            return {}

    async def delete_lead(self, url):
        async with self._lock:
            try:
                await self.conn.execute("DELETE FROM dynamic_queue WHERE url = ?", (url,))
                await self.conn.commit()
            except Exception as e:
                logger.error(f"Failed to delete lead {url}: {e}")

    async def close(self):
        if self.conn:
            await self.conn.close()
