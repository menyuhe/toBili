"""凭据保险库。"""

from .acl import audit, secure, tighten
from .dpapi import available as dpapi_available
from .filevault import OPEN, SEALED, FileVault, VaultError

__all__ = [
    "OPEN",
    "SEALED",
    "FileVault",
    "VaultError",
    "audit",
    "dpapi_available",
    "secure",
    "tighten",
]
