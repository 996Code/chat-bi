import base64
import hashlib
from cryptography.fernet import Fernet
from app.core.config import settings


def _get_fernet() -> Fernet:
    key = settings.data_source_encryption_key.encode()
    # Derive 32 url-safe base64-encoded bytes from any input string
    derived = hashlib.sha256(key).digest()
    key_b64 = base64.urlsafe_b64encode(derived)
    return Fernet(key_b64)


def encrypt_value(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()
