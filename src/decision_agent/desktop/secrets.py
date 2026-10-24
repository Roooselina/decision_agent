from __future__ import annotations

import os

SERVICE = "decision-agent"
API_KEY_NAME = "anthropic_api_key"


class SecretStoreUnavailable(RuntimeError):
    """Raised when the operating system credential store cannot be used."""


def _keyring():
    try:
        import keyring
        from keyring.errors import NoKeyringError
    except ImportError as exc:  # pragma: no cover - keyring is a declared dependency
        raise SecretStoreUnavailable("keyring 패키지가 없습니다.") from exc
    return keyring, NoKeyringError


def get_api_key() -> str | None:
    """Read the key from the OS credential store, then from ANTHROPIC_API_KEY."""
    try:
        keyring, no_keyring = _keyring()
        try:
            value = keyring.get_password(SERVICE, API_KEY_NAME)
        except no_keyring:
            value = None
        except Exception:  # noqa: BLE001 - a broken backend must not stop the app
            value = None
        if value:
            return value
    except SecretStoreUnavailable:
        pass
    return os.getenv("ANTHROPIC_API_KEY") or None


def set_api_key(value: str) -> None:
    keyring, no_keyring = _keyring()
    try:
        keyring.set_password(SERVICE, API_KEY_NAME, value)
    except no_keyring as exc:
        raise SecretStoreUnavailable("이 환경에는 사용할 수 있는 자격 증명 저장소가 없습니다.") from exc


def delete_api_key() -> None:
    keyring, no_keyring = _keyring()
    try:
        keyring.delete_password(SERVICE, API_KEY_NAME)
    except no_keyring:
        return
    except Exception:  # noqa: BLE001 - deleting a missing entry raises a backend specific error
        return


class KeyringSecrets:
    """SecretStore backed by the operating system credential store (used by the connectors)."""

    def get(self, name: str) -> str | None:
        try:
            keyring, no_keyring = _keyring()
            try:
                return keyring.get_password(SERVICE, name) or None
            except no_keyring:
                return None
            except Exception:  # noqa: BLE001 - a broken backend must not stop the app
                return None
        except SecretStoreUnavailable:
            return None

    def set(self, name: str, value: str) -> None:
        keyring, no_keyring = _keyring()
        try:
            keyring.set_password(SERVICE, name, value)
        except no_keyring as exc:
            raise SecretStoreUnavailable("이 환경에는 사용할 수 있는 자격 증명 저장소가 없습니다.") from exc

    def delete(self, name: str) -> None:
        keyring, no_keyring = _keyring()
        try:
            keyring.delete_password(SERVICE, name)
        except no_keyring:
            return
        except Exception:  # noqa: BLE001 - deleting a missing entry raises a backend specific error
            return
