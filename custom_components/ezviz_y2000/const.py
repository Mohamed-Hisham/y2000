"""Constants for the EZVIZ Y2000 integration."""

DOMAIN = "ezviz_y2000"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SERIAL = "serial_number"
CONF_REGION = "region"

# Lock-control parameters. These are device/account specific and EZVIZ does
# not document them. Defaults below work for most single-channel locks; expose
# them in the config flow so they can be overridden per device.
CONF_USER_ID = "user_id"
CONF_LOCK_NO = "lock_no"
CONF_LOCAL_INDEX = "local_index"
CONF_RESOURCE_ID = "resource_id"

DEFAULT_REGION = "eu"
DEFAULT_LOCK_NO = 1
# Y2000 reports its DoorLock resource at localIndex 0 (resolved live from the
# device's resourceInfos; this is only the fallback).
DEFAULT_LOCAL_INDEX = "0"
DEFAULT_RESOURCE_ID = "DoorLock"

# Seconds after a relock/unlock command before we re-poll real state.
RELOCK_GRACE_SECONDS = 25

# Legacy bind-code prefix used by pyezvizapi when no terminal bind is available.
# Must match the value in pyezvizapi.constants so the fallback PUT is identical
# to what the library would send.
try:
    from pyezvizapi.constants import FEATURE_CODE  # type: ignore[import-untyped]
except ImportError:
    FEATURE_CODE = ""
