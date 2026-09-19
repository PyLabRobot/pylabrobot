"""Encrypting and decrypting protocol files.

A protocol file is a DES-encrypted XML document. The cipher is CBC with a fixed key that is also
used as the initialisation vector, and PKCS7 padding.

DES is not in the standard library, so reading or writing a protocol file needs
``pycryptodome`` installed.
"""

from __future__ import annotations

from typing import cast

try:
  from Crypto.Cipher import DES
  from Crypto.Util.Padding import pad, unpad

  HAS_PYCRYPTODOME = True
  _IMPORT_ERROR = ""
except ImportError as error:  # pragma: no cover - depends on the environment
  HAS_PYCRYPTODOME = False
  _IMPORT_ERROR = str(error)

_KEY = b"%?#\x14?i`?"
_BLOCK_SIZE = 8
_ENCODING = "utf-8"


def _require_pycryptodome() -> None:
  """Check that the cipher is available.

  Raises:
    RuntimeError: If pycryptodome is not installed.
  """
  if not HAS_PYCRYPTODOME:
    raise RuntimeError(
      "To enable decryption of .LHC files install pycryptodome: pip install pycryptodome. "
      f"Import error: {_IMPORT_ERROR}"
    )


def decrypt(data: bytes) -> str:
  """Decrypt the contents of a protocol file.

  Args:
    data: The file's bytes.

  Returns:
    The XML document inside it.

  Raises:
    RuntimeError: If pycryptodome is not installed.
    ValueError: If the data is not a whole number of blocks, or its padding is wrong, which is
      what a file that is not a protocol looks like.
  """
  _require_pycryptodome()
  cipher = DES.new(_KEY, DES.MODE_CBC, _KEY)
  return cast(str, unpad(cipher.decrypt(data), _BLOCK_SIZE).decode(_ENCODING))


def encrypt(document: str) -> bytes:
  """Encrypt an XML document into the contents of a protocol file.

  Args:
    document: The document to encrypt.

  Returns:
    The bytes to write.

  Raises:
    RuntimeError: If pycryptodome is not installed.
  """
  _require_pycryptodome()
  cipher = DES.new(_KEY, DES.MODE_CBC, _KEY)
  return cast(bytes, cipher.encrypt(pad(document.encode(_ENCODING), _BLOCK_SIZE)))
