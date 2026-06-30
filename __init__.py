"""EZVIZ Y2000 smart lock integration for Home Assistant."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from pyezvizapi import EzvizClient

from .const import (
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SERIAL,
    CONF_USERNAME,
    DEFAULT_REGION,
    DOMAIN,
    RELOCK_GRACE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["lock", "sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EZVIZ Y2000 from a config entry."""
    client = EzvizClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_REGION, DEFAULT_REGION),
    )
    serial = entry.data[CONF_SERIAL].strip()

    # Initial blocking login in the executor.
    await hass.async_add_executor_job(client.login)

    async def async_update_data():
        """Poll device info (status, battery, wifi). Lock truth comes via listener."""
        def fetch():
            try:
                client.login()  # refresh/keepalive; cheap if token valid
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Login refresh failed (will retry): %s", err)
            return client.get_device_infos()

        return await hass.async_add_executor_job(fetch)

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=async_update_data,
        update_interval=timedelta(seconds=20),
    )

    # Shared runtime state used by entities.
    coordinator.client = client
    coordinator.serial = serial
    coordinator.entry = entry
    coordinator.doorbell_ringing = False
    coordinator.last_event = "Ready"
    coordinator.last_event_id = ""
    coordinator.lock_state = 0          # 0 = locked, 1 = unlocked (inferred)
    coordinator.unlock_time = 0.0
    coordinator.pending_command_until = 0.0  # optimistic state window

    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    async def fast_listener():
        """Near-real-time event listener that infers lock/door/bell state."""
        while True:
            try:
                def get_alarms():
                    return client.get_alarminfo(serial)

                response = await hass.async_add_executor_job(get_alarms)
                alarms = response.get("alarms", []) if response else []

                if isinstance(alarms, list) and alarms:
                    latest = alarms[0]
                    alarm_id = latest.get("alarmId")
                    msg_text = latest.get("alarmMessage", "") or ""

                    if alarm_id != coordinator.last_event_id:
                        coordinator.last_event_id = alarm_id
                        coordinator.last_event = msg_text
                        low = msg_text.lower()

                        if "unlock" in low or "open" in low:
                            coordinator.lock_state = 1
                            coordinator.unlock_time = time.time()
                        elif "lock" in low or "close" in low:
                            coordinator.lock_state = 0

                        if any(k in low for k in ("ring", "bell", "calling")):
                            coordinator.doorbell_ringing = True
                            coordinator.async_set_updated_data(coordinator.data)
                            await asyncio.sleep(7)
                            coordinator.doorbell_ringing = False

                        coordinator.async_set_updated_data(coordinator.data)

                # Auto-relock inference: Y2000 firmware often doesn't emit a
                # "locked" event, so fall back to locked after the grace window.
                now = time.time()
                if (
                    coordinator.lock_state == 1
                    and now > coordinator.pending_command_until
                    and (now - coordinator.unlock_time) > RELOCK_GRACE_SECONDS
                ):
                    coordinator.lock_state = 0
                    coordinator.async_set_updated_data(coordinator.data)

            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Listener error: %s", err)

            await asyncio.sleep(2)

    entry.async_create_background_task(hass, fast_listener(), "ezviz-y2000-listener")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
