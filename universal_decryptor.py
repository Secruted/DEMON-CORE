import sqlite3
import base64
import getpass
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.fernet import Fernet

def derive_key(password, salt):
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,  # Must match db_manager.py
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))

def find_salt(conn):
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT value FROM _internal_settings WHERE key = 'salt'")
        res = cursor.fetchone()
        if res:
            return res[0]
    except:
        pass
    return None

def main():
    db_path = 'watcher.db'
    password = getpass.getpass(prompt='Enter Master Password: ')
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        salt = find_salt(conn)
        
        if not salt:
            print("[-] Salt not found. Are you in the right directory?")
            return

        key = derive_key(password, salt)
        fernet = Fernet(key)

        cursor = conn.cursor()
        cursor.execute("SELECT leak_type, encrypted_value, source, timestamp FROM leaks ORDER BY timestamp DESC")
        rows = cursor.fetchall()

        print("\n" + "="*40)
        print("       OPENED VAULT - LEAKS")
        print("="*40)

        if not rows:
            print("[*] Vault is empty.")
        else:
            for row in rows:
                try:
                    decrypted = fernet.decrypt(row['encrypted_value']).decode()
                    print(f"\n[✔] Type   : {row['leak_type']}")
                    print(f"    Value  : {decrypted}")
                    print(f"    Source : {row['source']}")
                except Exception:
                    continue

        conn.close()
    except Exception as e:
        print(f"[-] Error: {e}")

if __name__ == "__main__":
    main()
