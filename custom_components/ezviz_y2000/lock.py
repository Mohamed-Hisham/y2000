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
_REMOTE_UNLOCK_SUFFIX = "/DoorLockMgr/RemoteUnlockReq"
_REMOTE_LOCK_SUFFIX = "/DoorLockMgr/RemoteLockReq"


def _iot_path(serial: str, resource_id: str, local_index: str, suffix: str) -> str:
    return f"{_IOT_ACTION}{serial}/{resource_id}/{local_index}{suffix}"


def _lock_payload(bind_code: str, lock_no: int, user_name: str) -> dict:
    return {"unLockInfo": {
        "bindCode": bind_code,
        "lockNo": lock_no,
        "streamToken": "",
        "userName": user_name,
    }}


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
        """Call remote_unlock, falling back to a direct PUT if unavailable."""
        client = self._client
        serial = self._serial
        resource_id = self._resource_id
        local_index = self._local_index
        user_id = self._user_id
        lock_no = self._lock_no

        if hasattr(client, "remote_unlock"):
            _LOGGER.debug("remote_unlock: using library method for %s", serial)
            result = client.remote_unlock(
                serial,
                user_id,
                lock_no,
                resource_id=resource_id,
                local_index=local_index,
            )
            _LOGGER.debug("remote_unlock: result=%s", result)
            return

        # Fallback for pyezvizapi < 1.0.5.0: replicate the PUT directly.
        _LOGGER.debug(
            "remote_unlock: library lacks remote_unlock, using direct PUT for %s",
            serial,
        )
        bind_code = f"{FEATURE_CODE}{user_id}"
        path = _iot_path(serial, resource_id, local_index, _REMOTE_UNLOCK_SUFFIX)
        payload = _lock_payload(bind_code, lock_no, user_id)
        resp = client._request_json("PUT", path, json_body=payload, retry_401=True)
        _LOGGER.debug(
            "remote_unlock fallback: serial=%s path=%s response=%s",
            serial,
            path,
            resp,
        )

    def _do_remote_lock(self) -> None:
        """Call remote_lock, falling back to a direct PUT if unavailable."""
        client = self._client
        serial = self._serial
        resource_id = self._resource_id
        local_index = self._local_index
        user_id = self._user_id
        lock_no = self._lock_no

        if hasattr(client, "remote_lock"):
            _LOGGER.debug("remote_lock: using library method for %s", serial)
            result = client.remote_lock(
                serial,
                user_id,
                lock_no,
                resource_id=resource_id,
                local_index=local_index,
            )
            _LOGGER.debug("remote_lock: result=%s", result)
            return

        _LOGGER.debug(
            "remote_lock: library lacks remote_lock, using direct PUT for %s",
            serial,
        )
        bind_code = f"{FEATURE_CODE}{user_id}"
        path = _iot_path(serial, resource_id, local_index, _REMOTE_LOCK_SUFFIX)
        payload = _lock_payload(bind_code, lock_no, user_id)
        resp = client._request_json("PUT", path, json_body=payload, retry_401=True)
        _LOGGER.debug(
            "remote_lock fallback: serial=%s path=%s response=%s",
            serial,
            path,
            resp,
        )

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
