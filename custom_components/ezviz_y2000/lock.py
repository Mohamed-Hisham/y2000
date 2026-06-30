"""Lock platform for EZVIZ Y2000 — the part the DL03 Pro repo was missing."""
from __future__ import annotations

import logging
import time

from homeassistant.components.lock import LockEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_LOCAL_INDEX,
    CONF_LOCK_NO,
    CONF_RESOURCE_ID,
    CONF_USER_ID,
    DEFAULT_LOCAL_INDEX,
    DEFAULT_LOCK_NO,
    DEFAULT_RESOURCE_ID,
    DOMAIN,
    FEATURE_CODE,
    RELOCK_GRACE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# Paths relative to the API base URL (https://<api_url>).
_IOT_ACTION = "/v3/iot-feature/action/"
_TERMINALS = "/v3/terminals"
_REMOTE_UNLOCK_SUFFIX = "/DoorLockMgr/RemoteUnlockReq"
_REMOTE_LOCK_SUFFIX = "/DoorLockMgr/RemoteLockReq"
# Terminal name the pyezvizapi login registers under; prefer its bind code.
_TERMINAL_NAME = "Hassio"


def _iot_path(serial: str, resource_id: str, local_index: str, suffix: str) -> str:
    return f"{_IOT_ACTION}{serial}/{resource_id}/{local_index}{suffix}"


def _lock_payload(bind_code: str, lock_no: int, user_name: str) -> dict:
    return {"unLockInfo": {
        "bindCode": bind_code,
        "lockNo": lock_no,
        "streamToken": "",
        "userName": user_name,
    }}


def _resolve_bind_code(client, user_id: str) -> tuple[str, str]:
    """Return (bindCode, userName) for the lock payload.

    EZVIZ rejects (HTTP 400) the legacy ``FEATURE_CODE + user_id`` bind code on
    many accounts. The correct bind code comes from the account's terminal info
    (``sign + userId``). Prefer the library helper; otherwise fetch it directly.
    Fall back to the legacy code only if no terminal bind is available.
    """
    # Native helper (pyezvizapi >= 1.0.5.0).
    if hasattr(client, "get_latest_terminal_bind"):
        try:
            bind_code, user_name = client.get_latest_terminal_bind(
                terminal_name=_TERMINAL_NAME
            )
            _LOGGER.debug("bind code via library terminal bind: userName=%s", user_name)
            return bind_code, user_name
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("library terminal bind unavailable: %s", err)

    # Raw fetch of /v3/terminals for older library versions.
    try:
        url = f"https://{client._token['api_url']}{_TERMINALS}"
        resp = client._session.get(url, params={"limit": 20, "offset": 0}, timeout=30)
        resp.raise_for_status()
        terminals = (resp.json() or {}).get("terminals") or []
        items = [
            t for t in terminals
            if isinstance(t, dict)
            and str(t.get("sign") or "").strip()
            and str(t.get("userId") or "").strip()
        ]
        named = [
            t for t in items
            if str(t.get("name") or t.get("terminalName") or "").casefold()
            == _TERMINAL_NAME.casefold()
        ]
        chosen = named or items
        if chosen:
            best = max(
                chosen,
                key=lambda t: str(t.get("lastModifytime") or t.get("lastModifyTime") or ""),
            )
            sign = str(best["sign"]).strip()
            term_uid = str(best["userId"]).strip()
            user_name = best.get("name") or best.get("terminalName") or term_uid
            _LOGGER.debug("bind code via raw terminals fetch: userName=%s", user_name)
            return f"{sign}{term_uid}", str(user_name)
        _LOGGER.debug("no usable terminal bind found in /v3/terminals")
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("raw terminal bind fetch failed: %s", err)

    _LOGGER.debug("falling back to legacy bind code (FEATURE_CODE + user_id)")
    return f"{FEATURE_CODE}{user_id}", user_id


def _raw_put(client, path: str, payload: dict, label: str, serial: str) -> None:
    """PUT via the client's raw requests.Session — works on all pyezvizapi versions.

    _request_json was added in 1.0.5.0; _session and _token['api_url'] have been
    present since the earliest 1.x releases, so this is the safest fallback.
    """
    api_url = client._token["api_url"]
    url = f"https://{api_url}{path}"
    _LOGGER.debug("%s fallback PUT: serial=%s url=%s payload=%s", label, serial, url, payload)
    resp = client._session.put(url, json=payload, timeout=30)
    body = resp.text[:500]
    _LOGGER.debug(
        "%s fallback response: serial=%s status=%s body=%s",
        label, serial, resp.status_code, body,
    )
    if not resp.ok:
        # Surface the EZVIZ response body so the HA action toast is actionable.
        raise RuntimeError(
            f"{label} failed: HTTP {resp.status_code} for {url} — response: {body}"
        )


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Y2000 lock entity."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([EzvizY2000Lock(coordinator, entry)])


class EzvizY2000Lock(CoordinatorEntity, LockEntity):
    """A controllable EZVIZ Y2000 lock.

    EZVIZ's cloud only exposes a momentary 'remote unlock' on most locks; the
    deadbolt re-engages physically. We therefore present unlock as the real
    action and treat lock() as best-effort (calls remote_lock if supported,
    otherwise just resets optimistic state).
    """

    _attr_has_entity_name = True
    _attr_name = "Lock"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._entry = entry
        self._client = coordinator.client
        self._serial = coordinator.serial
        self._attr_unique_id = f"{self._serial}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._serial)},
            name=f"EZVIZ Y2000 ({self._serial})",
            manufacturer="EZVIZ",
            model="Y2000",
        )
        data = entry.data
        self._user_id = str(data[CONF_USER_ID])
        self._lock_no = int(data.get(CONF_LOCK_NO, DEFAULT_LOCK_NO))
        self._local_index = str(data.get(CONF_LOCAL_INDEX, DEFAULT_LOCAL_INDEX))
        self._resource_id = data.get(CONF_RESOURCE_ID, DEFAULT_RESOURCE_ID)

    @property
    def is_locked(self) -> bool | None:
        return getattr(self.coordinator, "lock_state", 0) == 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_remote_unlock(self) -> None:
        """Unlock via a raw PUT so we control the bind code and surface errors.

        We deliberately do not defer to ``client.remote_unlock`` because that
        path silently falls back to the legacy bind code (which EZVIZ rejects
        with HTTP 400 on many accounts) and hides the response body. Building the
        request here lets us resolve the terminal bind code and log the exact
        EZVIZ error if the call fails.
        """
        client = self._client
        bind_code, user_name = _resolve_bind_code(client, self._user_id)
        path = _iot_path(
            self._serial, self._resource_id, self._local_index, _REMOTE_UNLOCK_SUFFIX
        )
        payload = _lock_payload(bind_code, self._lock_no, user_name)
        _raw_put(client, path, payload, "remote_unlock", self._serial)

    def _do_remote_lock(self) -> None:
        """Lock via a raw PUT (see ``_do_remote_unlock`` for rationale)."""
        client = self._client
        bind_code, user_name = _resolve_bind_code(client, self._user_id)
        path = _iot_path(
            self._serial, self._resource_id, self._local_index, _REMOTE_LOCK_SUFFIX
        )
        payload = _lock_payload(bind_code, self._lock_no, user_name)
        _raw_put(client, path, payload, "remote_lock", self._serial)

    # ------------------------------------------------------------------
    # HA entity actions
    # ------------------------------------------------------------------

    async def async_unlock(self, **kwargs) -> None:
        """Send remote unlock command."""
        try:
            await self.hass.async_add_executor_job(self._do_remote_unlock)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Remote unlock failed for %s: %s", self._serial, err)
            raise

        # Optimistic state; protect it from the listener's auto-relock briefly.
        self.coordinator.lock_state = 1
        self.coordinator.unlock_time = time.time()
        self.coordinator.pending_command_until = time.time() + RELOCK_GRACE_SECONDS
        self.coordinator.async_set_updated_data(self.coordinator.data)

    async def async_lock(self, **kwargs) -> None:
        """Best-effort remote lock (many Y2000 units relock physically)."""
        try:
            await self.hass.async_add_executor_job(self._do_remote_lock)
        except Exception as err:  # noqa: BLE001
            # Not all firmware supports remote lock; degrade gracefully.
            _LOGGER.warning("Remote lock not confirmed for %s: %s", self._serial, err)

        self.coordinator.lock_state = 0
        self.coordinator.pending_command_until = 0.0
        self.coordinator.async_set_updated_data(self.coordinator.data)
