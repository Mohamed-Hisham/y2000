"""Sensors for EZVIZ Y2000 (battery, wifi, last event, error count)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    serial = coordinator.serial
    async_add_entities(
        [
            EzvizBatterySensor(coordinator, serial),
            EzvizEventSensor(coordinator, serial),
            EzvizWifiSignalSensor(coordinator, serial),
            EzvizWifiSSIDSensor(coordinator, serial),
            EzvizErrorCountSensor(coordinator, serial),
        ]
    )


class _Base(CoordinatorEntity, SensorEntity):
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


class EzvizBatterySensor(_Base):
    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_battery"

    @property
    def native_value(self):
        power = (
            self._dev()
            .get("STATUS", {})
            .get("optionals", {})
            .get("multiPower", [])
        )
        if power and isinstance(power, list):
            return power[0].get("Remaining")
        return None


class EzvizEventSensor(_Base):
    _attr_name = "Last Event"
    _attr_icon = "mdi:history"

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_event"

    @property
    def native_value(self):
        return getattr(self.coordinator, "last_event", None)


class EzvizWifiSignalSensor(_Base):
    _attr_name = "Wi-Fi Signal"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_wifi_signal"

    @property
    def native_value(self):
        return self._dev().get("WIFI", {}).get("signal")


class EzvizWifiSSIDSensor(_Base):
    _attr_name = "Wi-Fi Network"
    _attr_icon = "mdi:wifi-cog"

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_wifi_ssid"

    @property
    def native_value(self):
        return self._dev().get("WIFI", {}).get("ssid")


class EzvizErrorCountSensor(_Base):
    _attr_name = "Failed Attempts"
    _attr_icon = "mdi:alert-lock"

    def __init__(self, coordinator, serial):
        super().__init__(coordinator, serial)
        self._attr_unique_id = f"{serial}_error_count"

    @property
    def native_value(self):
        feat = self._dev().get("FEATURE_INFO", {}).get("0", {})
        return (
            feat.get("DoorLock", {})
            .get("DoorLockMgr", {})
            .get("TryErrLock", {})
            .get("errCount", 0)
        )
