import sqlite3
import hashlib
import base64
import os
import logging
import time

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.exceptions import InvalidKey

logger = logging.getLogger(__name__)

class DBManager:
    def __init__(self, db_path, password):
        """Initializes the encrypted database, managing the salt securely."""
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()

        self._initialize_encryption(password)
        self._create_tables()

    def _get_or_create_salt(self):
        """Retrieves or creates encryption salt."""
        self.cursor.execute("CREATE TABLE IF NOT EXISTS _internal_settings (key TEXT PRIMARY KEY, value BLOB)")
        self.cursor.execute("SELECT value FROM _internal_settings WHERE key = 'salt'")
        result = self.cursor.fetchone()

        if result:
            salt = result[0]
            logger.info("Retrieved existing encryption salt from DB.")
        else:
            salt = os.urandom(16)
            self.cursor.execute("INSERT INTO _internal_settings (key, value) VALUES ('salt', ?)", (salt,))
            self.conn.commit()
            logger.info("Generated and stored new encryption salt in DB.")
        return salt

    def _initialize_encryption(self, password):
        """Generates encryption key from password."""
        salt = self._get_or_create_salt()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        try:
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
            self.cipher_suite = Fernet(key)
        except InvalidKey as e:
            logger.critical(f"Failed to derive encryption key: {e}. Check master password.")
            raise

    def _encrypt(self, data):
        """Encrypts plaintext data."""
        return self.cipher_suite.encrypt(data.encode('utf-8'))

    def _decrypt(self, encrypted_data):
        """Decrypts encrypted data."""
        try:
            return self.cipher_suite.decrypt(encrypted_data).decode('utf-8')
        except Exception as e:
            logger.error(f"Decryption error: {e}.", exc_info=True)
            return "[DECRYPTION_ERROR]"

    def _create_tables(self):
        """Creates all necessary tables including the new dynamic_queue."""
        self.cursor.execute("CREATE TABLE IF NOT EXISTS assets (asset_hash TEXT PRIMARY KEY, asset_type TEXT, encrypted_value BLOB, source TEXT, timestamp REAL)")
        self.cursor.execute("CREATE TABLE IF NOT EXISTS leaks (leak_hash TEXT PRIMARY KEY, leak_type TEXT, encrypted_value BLOB, source TEXT, timestamp REAL)")
        self.cursor.execute("CREATE TABLE IF NOT EXISTS pending_alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, encrypted_message BLOB, timestamp REAL)")
        
        # New table for Persistence Layer
        self.cursor.execute("CREATE TABLE IF NOT EXISTS dynamic_queue (url TEXT PRIMARY KEY, origin TEXT, rep INTEGER, birth REAL)")
        
        self.conn.commit()

    # --- NEW PERSISTENCE METHODS ---

    def save_lead(self, url, origin):
        """Saves a discovered lead to DB immediately to prevent data loss."""
        try:
            query = "INSERT OR REPLACE INTO dynamic_queue (url, origin, rep, birth) VALUES (?, ?, ?, ?)"
            self.cursor.execute(query, (url, origin, 100, time.time()))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to save lead {url}: {e}")

    def load_dynamic_queue(self):
        """Recovers all saved leads from DB on system startup."""
        try:
            self.cursor.execute("SELECT url, rep, birth FROM dynamic_queue")
            rows = self.cursor.fetchall()
            return {row[0]: {'rep': row[1], 'birth': row[2], 'fails': 0} for row in rows}
        except Exception as e:
            logger.error(f"Failed to load dynamic queue: {e}")
            return {}

    def delete_lead(self, url):
        """Deletes a lead from DB when it expires (TTL)."""
        try:
            self.cursor.execute("DELETE FROM dynamic_queue WHERE url = ?", (url,))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Failed to delete lead {url}: {e}")

    # --- EXISTING ASSET & LEAK METHODS ---

    def _hash_item(self, item_value):
        return hashlib.sha256(item_value.encode('utf-8')).hexdigest()

    def add_asset(self, asset_type, asset_value, source):
        asset_hash = self._hash_item(asset_value)
        self.cursor.execute("SELECT 1 FROM assets WHERE asset_hash = ?", (asset_hash,))
        if self.cursor.fetchone(): return
        encrypted_value = self._encrypt(asset_value)
        self.cursor.execute("INSERT INTO assets (asset_hash, asset_type, encrypted_value, source, timestamp) VALUES (?, ?, ?, ?, ?)",(asset_hash, asset_type, encrypted_value, source, time.time()))
        self.conn.commit()

    def add_leak(self, leak_type, leak_value, source):
        leak_hash = self._hash_item(leak_value)
        self.cursor.execute("SELECT 1 FROM leaks WHERE leak_hash = ?", (leak_hash,))
        if self.cursor.fetchone(): return
        encrypted_value = self._encrypt(leak_value)
        self.cursor.execute("INSERT INTO leaks (leak_hash, leak_type, encrypted_value, source, timestamp) VALUES (?, ?, ?, ?, ?)",(leak_hash, leak_type, encrypted_value, source, time.time()))
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
