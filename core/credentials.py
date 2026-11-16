"""Optional password storage in the Windows Credential Manager (via keyring)."""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)
SERVICE = "NetcoolShiftMonitor"

try:
    import keyring  # type: ignore
except Exception:  # pragma: no cover
    keyring = None


class CredentialStore:
    def __init__(self, service: str = SERVICE):
        self.service = service              # "NetcoolShiftMonitor" (MOM) or "NetcoolShiftMonitor-Remedy"

    @property
    def available(self) -> bool:
        return keyring is not None

    def get(self, user: str) -> Optional[str]:
        if not (keyring and user):
            return None
        try:
            return keyring.get_password(self.service, user)
        except Exception:
            log.warning("keyring read failed", exc_info=True)
            return None

    def save(self, user: str, password: str) -> None:
        if keyring and user:
            try:
                keyring.set_password(self.service, user, password)
            except Exception:
                log.warning("keyring write failed", exc_info=True)

    def delete(self, user: str) -> None:
        if keyring and user:
            try:
                keyring.delete_password(self.service, user)
            except Exception:
                pass
