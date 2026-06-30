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
    RELOCK_GRACE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


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

    async def async_unlock(self, **kwargs) -> None:
        """Send remote unlock command."""
        def _unlock():
            return self._client.remote_unlock(
                self._serial,
                self._user_id,
                self._lock_no,
                resource_id=self._resource_id,
                local_index=self._local_index,
            )

        try:
            await self.hass.async_add_executor_job(_unlock)
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
        def _lock():
            return self._client.remote_lock(
                self._serial,
                self._user_id,
                self._lock_no,
                resource_id=self._resource_id,
                local_index=self._local_index,
            )

        try:
            await self.hass.async_add_executor_job(_lock)
        except Exception as err:  # noqa: BLE001
            # Not all firmware supports remote_lock; degrade gracefully.
            _LOGGER.warning("Remote lock not confirmed for %s: %s", self._serial, err)

        self.coordinator.lock_state = 0
        self.coordinator.pending_command_until = 0.0
        self.coordinator.async_set_updated_data(self.coordinator.data)
