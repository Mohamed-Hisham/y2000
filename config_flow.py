"""Config flow for EZVIZ Y2000."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries

from pyezvizapi import EzvizClient

from .const import (
    CONF_LOCAL_INDEX,
    CONF_LOCK_NO,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_RESOURCE_ID,
    CONF_SERIAL,
    CONF_USER_ID,
    CONF_USERNAME,
    DEFAULT_LOCAL_INDEX,
    DEFAULT_LOCK_NO,
    DEFAULT_REGION,
    DEFAULT_RESOURCE_ID,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _try_discover_user_id(client: EzvizClient, serial: str) -> str | None:
    """Best-effort: pull the owner/admin user id from the door-lock users list."""
    try:
        result = client.get_door_lock_users(serial)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not auto-discover door lock users: %s", err)
        return None

    # Shape varies by firmware; look for an admin/owner entry first.
    users = (
        result.get("userInfos")
        or result.get("users")
        or result.get("data")
        or []
    )
    if isinstance(users, dict):
        users = users.get("list") or list(users.values())
    if not isinstance(users, list):
        return None

    admin = None
    for u in users:
        if not isinstance(u, dict):
            continue
        role = str(u.get("role") or u.get("userType") or "").lower()
        uid = u.get("userId") or u.get("id") or u.get("userCode")
        if uid is None:
            continue
        if "admin" in role or "owner" in role or u.get("isAdmin"):
            admin = str(uid)
            break
        if admin is None:
            admin = str(uid)
    return admin


class EzvizY2000ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self):
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            client = EzvizClient(
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_REGION, DEFAULT_REGION),
            )
            serial = user_input[CONF_SERIAL].strip()

            def _login_and_discover():
                client.login()
                return _try_discover_user_id(client, serial)

            try:
                discovered_uid = await self.hass.async_add_executor_job(
                    _login_and_discover
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("EZVIZ login failed: %s", err)
                errors["base"] = "invalid_auth"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                self._data = dict(user_input)
                self._data[CONF_SERIAL] = serial
                if discovered_uid:
                    self._data[CONF_USER_ID] = discovered_uid
                return await self.async_step_lock_params()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SERIAL): str,
                vol.Optional(CONF_REGION, default=DEFAULT_REGION): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_lock_params(self, user_input=None):
        """Confirm/override the lock-control parameters."""
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(
                title=f"EZVIZ Y2000 ({self._data[CONF_SERIAL]})",
                data=self._data,
            )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USER_ID, default=self._data.get(CONF_USER_ID, "")
                ): str,
                vol.Optional(CONF_LOCK_NO, default=DEFAULT_LOCK_NO): int,
                vol.Optional(CONF_LOCAL_INDEX, default=DEFAULT_LOCAL_INDEX): str,
                vol.Optional(CONF_RESOURCE_ID, default=DEFAULT_RESOURCE_ID): str,
            }
        )
        return self.async_show_form(step_id="lock_params", data_schema=schema)
