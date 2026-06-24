"""用户 secrets 存取（内网明文存库，列名 secret_value_enc 历史保留）。"""


def encrypt_secret(plaintext: str) -> str:
    if plaintext is None:
        return ""
    return plaintext


def decrypt_secret(stored: str) -> str:
    if stored is None:
        return ""
    return stored
