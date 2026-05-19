from cryptography.fernet import Fernet
import os

# This key should be in .env as SOS_VAULT_KEY
MASTER_KEY = os.getenv("SOS_VAULT_KEY", Fernet.generate_key().decode())

def encrypt_token(token: str) -> str:
    f = Fernet(MASTER_KEY.encode())
    return f.encrypt(token.encode()).decode()

def decrypt_token(encrypted_token: str) -> str:
    f = Fernet(MASTER_KEY.encode())
    return f.decrypt(encrypted_token.encode()).decode()
