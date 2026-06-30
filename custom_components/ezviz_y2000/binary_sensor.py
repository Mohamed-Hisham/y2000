"""Binary sensors for EZVIZ Y2000 (door, doorbell)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    serial = coordinator.serial
    async_add_entities(
        [
            EzvizDoorBinarySensor(coordinator, serial),
            EzvizBellBinarySensor(coordinator, serial),
        ]
    )


class _Base(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, serial):
        super().__init__(coordinator)
        self.serial = serial
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            name=f"EZVIZ Y2000 ({serial})",
            manufacturer="EZVIZ",
            model="Y2000",
        )

    def _dev(self) -> dict:
        return (self.coordinator.data or {}).get(self.serial, {}) or {}


class EzvizDoorBinarySensor(_Base):
    _attr_name = "Door"
    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_door_status"

    @property
    def is_on(self):
        return (
            self._dev().get("STATUS", {}).get("optionals", {}).get("dlDoor") == 1
        )


class EzvizBellBinarySensor(_Base):
    _attr_name = "Doorbell"
    _attr_device_class = BinarySensorDeviceClass.SOUND

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_bell_status"

    @property
    def is_on(self):
        return getattr(self.coordinator, "doorbell_ringing", False)
