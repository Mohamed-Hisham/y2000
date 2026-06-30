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
    CONF_UNLOCK_USERNAME,
    CONF_USER_ID,
    CONF_USERNAME,
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
_DOORLOCK_USERS = "/v3/doorlocks/"
_REMOTE_UNLOCK_SUFFIX = "/DoorLockMgr/RemoteUnlockReq"
_REMOTE_LOCK_SUFFIX = "/DoorLockMgr/RemoteLockReq"
# Terminal name the pyezvizapi login registers under; prefer its bind code.
_TERMINAL_NAME = "Hassio"
# pyezvizapi's Camera.door_unlock uses lockNo 2 for the door (gate = 1). This is
# a fixed convention, independent of the door-lock user index.
_DOOR_LOCK_NO = 2


def _dump_lock_diagnostics(client, serial: str, device: dict | None = None) -> None:
    """Log door-lock users, terminals, and the device's resourceInfos to help
    find the right ``resourceId`` / ``localIndex`` / ``lockNo`` / ``userName``
    when the device rejects a command ("manage failed").

    Logged at ERROR so it shows without enabling debug. This is the user's own
    account data in their own logs.
    """
    # Device resourceInfos: the authoritative source for the lock's resourceId
    # and localIndex (we currently hardcode "DoorLock"/"1", which the device's
    # feature manager may not accept).
    if device:
        res = device.get("resourceInfos") or device.get("RESOURCE") or "<none>"
        _LOGGER.error(
            "EZVIZ Y2000 diagnostics [resourceInfos] %s | device_keys=%s",
            res, sorted(device.keys()),
        )

    base = f"https://{client._token['api_url']}"
    for label, url in (
        ("doorlock_users", f"{base}{_DOORLOCK_USERS}{serial}/users"),
        ("terminals", f"{base}{_TERMINALS}"),
    ):
        try:
            params = {"limit": 20, "offset": 0} if label == "terminals" else None
            resp = client._session.get(url, params=params, timeout=30)
            _LOGGER.error(
                "EZVIZ Y2000 diagnostics [%s] status=%s body=%s",
                label, resp.status_code, resp.text[:1500],
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("EZVIZ Y2000 diagnostics [%s] fetch failed: %s", label, err)


def _iot_path(serial: str, resource_id: str, local_index: str, suffix: str) -> str:
    return f"{_IOT_ACTION}{serial}/{resource_id}/{local_index}{suffix}"


def _resolve_route(device: dict | None, default_resource: str, default_index: str):
    """Resolve (resourceIdentifier, localIndex) for the IoT action route.

    Empirically, the route segment for this device is the ``resourceIdentifier``
    string ("DoorLock") — that path reaches the device's DoorLockMgr feature.
    The ``resourceId`` UUID, by contrast, returns "设备功能未报备" ("device
    function not reported"), i.e. that resource has no such feature. The Y2000
    reports ``localIndex="0"``. Prefer the DoorLock entry; fall back to the
    configured/default values when absent.
    """
    infos = (device or {}).get("resourceInfos") or []
    doorlock = None
    for res in infos:
        if not isinstance(res, dict):
            continue
        category = str(res.get("resourceCategory") or res.get("resourceIdentifier") or "")
        if category.casefold() == "doorlock":
            doorlock = res
            break
    # Fall back to the first resource entry if no explicit DoorLock category.
    if doorlock is None:
        doorlock = next((r for r in infos if isinstance(r, dict)), None)

    if doorlock:
        resource_id = doorlock.get("resourceIdentifier") or default_resource
        local_index = str(doorlock.get("localIndex", default_index))
        _LOGGER.debug(
            "resolved route from resourceInfos: resource=%s local_index=%s",
            resource_id, local_index,
        )
        return resource_id, local_index
    return default_resource, default_index


def _lock_payload(bind_code: str, lock_no: int, user_name: str) -> dict:
    return {"unLockInfo": {
        "bindCode": bind_code,
        "lockNo": lock_no,
        "streamToken": "",
        "userName": user_name,
    }}


def _fetch_terminals(client) -> list[dict]:
    """Return the account's terminal entries (each has sign + userId)."""
    try:
        url = f"https://{client._token['api_url']}{_TERMINALS}"
        resp = client._session.get(url, params={"limit": 20, "offset": 0}, timeout=30)
        resp.raise_for_status()
        terminals = (resp.json() or {}).get("terminals") or []
        return [
            t for t in terminals
            if isinstance(t, dict)
            and str(t.get("sign") or "").strip()
            and str(t.get("userId") or "").strip()
        ]
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("terminal fetch failed: %s", err)
        return []


def _fetch_lock_users(client, serial: str) -> list[dict]:
    """Return the lock's enrolled users (each has index + name/realName)."""
    try:
        url = f"https://{client._token['api_url']}{_DOORLOCK_USERS}{serial}/users"
        resp = client._session.get(url, timeout=30)
        resp.raise_for_status()
        return [u for u in (resp.json() or {}).get("users") or [] if isinstance(u, dict)]
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("lock user fetch failed: %s", err)
        return []


def _bind_codes(client, user_id: str) -> list[tuple[str, str, str]]:
    """Ordered list of (bindCode, terminalName, source).

    ``bindCode`` is ``sign + userId`` and ``sign`` is per-login-session. We try
    every terminal's bind code (newest "Hassio" first, then others) plus the
    legacy code, and stop at whichever the device accepts.
    """
    terminals = _fetch_terminals(client)
    hassio = [
        t for t in terminals
        if str(t.get("name") or t.get("terminalName") or "").casefold()
        == _TERMINAL_NAME.casefold()
    ]
    others = [t for t in terminals if t not in hassio]
    ordered = sorted(
        hassio, key=lambda t: str(t.get("lastModifytime") or t.get("lastModifyTime") or ""),
        reverse=True,
    ) + others

    codes: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for t in ordered:
        sign = str(t["sign"]).strip()
        term_uid = str(t["userId"]).strip()
        name = str(t.get("name") or t.get("terminalName") or term_uid)
        bind = f"{sign}{term_uid}"
        if bind not in seen:
            seen.add(bind)
            codes.append((bind, name, f"terminal:{name}:{sign[:8]}"))

    legacy = f"{FEATURE_CODE}{user_id}"
    if legacy not in seen:
        codes.append((legacy, user_id, "legacy:feature_code"))
    return codes


def _user_names(client, serial: str, terminal_names: list[str], user_id: str,
                primary: str | None = None) -> list[str]:
    """Ordered userName candidates for the unlock payload.

    The configured unlock username (the account email, set on the config page)
    is tried first, then the enrolled door-lock users (e.g. "Dudu"), then the
    account/terminal names — sweeping is just a fallback if the configured value
    is wrong.
    """
    names: list[str] = []

    def add(v):
        v = str(v or "").strip()
        if v and v not in names:
            names.append(v)

    add(primary)
    for u in _fetch_lock_users(client, serial):
        add(u.get("name"))
        add(u.get("remarkName"))
        add(u.get("realName"))
        add(u.get("index"))
    account = str(getattr(client, "_token", {}).get("username", ""))
    add(account)
    for n in terminal_names:
        add(n)
    add(user_id)
    return names


def _try_put(client, path: str, payload: dict, serial: str) -> tuple[bool, int, str]:
    """PUT and return (ok, status, body) without raising.

    ``ok`` is True only when the HTTP status is 2xx *and* the EZVIZ meta code is
    a success code (200). A 200 envelope can still carry a device error such as
    ``meta.code == 3`` ("manage failed"), which must not count as success.
    """
    url = f"https://{client._token['api_url']}{path}"
    resp = client._session.put(url, json=payload, timeout=30)
    body = resp.text[:500]
    meta_code = None
    try:
        meta_code = (resp.json() or {}).get("meta", {}).get("code")
    except Exception:  # noqa: BLE001
        pass
    ok = resp.ok and meta_code in (200, 0)
    _LOGGER.debug(
        "PUT %s -> status=%s meta=%s ok=%s body=%s",
        path, resp.status_code, meta_code, ok, body,
    )
    return ok, resp.status_code, body


def _remote_action(client, serial: str, resource_id: str, local_index: str,
                   suffix: str, lock_no: int, user_id: str, label: str,
                   unlock_username: str | None = None) -> None:
    """Try userName x bindCode combinations until the device accepts the command.

    The configured unlock username (account email) is tried first; if it is
    wrong we fall back to sweeping the enrolled door-lock users (e.g. "Dudu")
    and terminal names against every bind code, stopping at the first
    combination the device accepts.
    """
    codes = _bind_codes(client, user_id)
    if not codes:
        raise RuntimeError(f"{label} failed: no bind-code candidates available")
    terminal_names = [name for _, name, _ in codes]
    user_names = _user_names(client, serial, terminal_names, user_id, unlock_username)

    path = _iot_path(serial, resource_id, local_index, suffix)
    last_status, last_body = 0, ""
    attempts = 0
    for user_name in user_names:
        for bind_code, _name, source in codes:
            attempts += 1
            payload = _lock_payload(bind_code, lock_no, user_name)
            ok, status, body = _try_put(client, path, payload, serial)
            if ok:
                _LOGGER.debug(
                    "%s succeeded: userName=%s via %s", label, user_name, source
                )
                return
            last_status, last_body = status, body

    raise RuntimeError(
        f"{label} failed after {attempts} attempt(s) "
        f"({len(user_names)} userName x {len(codes)} bindCode); "
        f"last HTTP {last_status} for {path} — response: {last_body}"
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
        # userName for the unlock payload — the account email by default.
        self._unlock_username = (
            data.get(CONF_UNLOCK_USERNAME) or data.get(CONF_USERNAME) or ""
        )

    @property
    def is_locked(self) -> bool | None:
        return getattr(self.coordinator, "lock_state", 0) == 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _do_remote_unlock(self) -> None:
        """Unlock via raw PUT, trying each bind-code candidate (see _remote_action)."""
        resource_id, local_index = self._route()
        _remote_action(
            self._client, self._serial, resource_id, local_index,
            _REMOTE_UNLOCK_SUFFIX, _DOOR_LOCK_NO, self._user_id, "remote_unlock",
            self._unlock_username,
        )

    def _do_remote_lock(self) -> None:
        """Lock via raw PUT, trying each bind-code candidate."""
        resource_id, local_index = self._route()
        _remote_action(
            self._client, self._serial, resource_id, local_index,
            _REMOTE_LOCK_SUFFIX, _DOOR_LOCK_NO, self._user_id, "remote_lock",
            self._unlock_username,
        )

    def _route(self) -> tuple[str, str]:
        """Resolve (resourceIdentifier, localIndex) preferring the device's
        resourceInfos over the configured defaults."""
        device = (self.coordinator.data or {}).get(self._serial, {})
        return _resolve_route(device, self._resource_id, self._local_index)

    # ------------------------------------------------------------------
    # HA entity actions
    # ------------------------------------------------------------------

    async def async_unlock(self, **kwargs) -> None:
        """Send remote unlock command."""
        try:
            await self.hass.async_add_executor_job(self._do_remote_unlock)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Remote unlock failed for %s: %s", self._serial, err)
            device = (self.coordinator.data or {}).get(self._serial, {})
            await self.hass.async_add_executor_job(
                _dump_lock_diagnostics, self._client, self._serial, device
            )
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
            device = (self.coordinator.data or {}).get(self._serial, {})
            await self.hass.async_add_executor_job(
                _dump_lock_diagnostics, self._client, self._serial, device
            )

        self.coordinator.lock_state = 0
        self.coordinator.pending_command_until = 0.0
        self.coordinator.async_set_updated_data(self.coordinator.data)
