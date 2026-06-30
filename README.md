# EZVIZ Y2000 Lock — Home Assistant custom integration

A controllable Home Assistant integration for the **EZVIZ Y2000** smart lock.
Adapted from [iwanstudio/ha-ezviz-dl03-pro](https://github.com/iwanstudio/ha-ezviz-dl03-pro),
with the key addition the original was missing: a **real `lock` entity** that
actually unlocks the door via the EZVIZ cloud API, not just a read-only status guess.

It exposes:

- `lock.ezviz_y2000_lock` — **remote unlock** (and best-effort remote lock)
- `sensor` — battery %, Wi-Fi signal, Wi-Fi SSID, last event, failed attempts
- `binary_sensor` — door open/closed, doorbell ringing
- Near-real-time event listener (≈2 s) for doorbell / unlock events

Built on `pyezvizapi >= 1.0.5.0`, which provides `remote_unlock` / `remote_lock`.

---

## Install

1. Copy the `ezviz_y2000` folder into `config/custom_components/` on your HA host.
   (Or add this repo to HACS as a custom repository, category *Integration*.)
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → "EZVIZ Y2000 Lock"**.
4. Enter your EZVIZ **email, password, device serial number**, and region
   (`eu`, `us`, etc. — match the region your EZVIZ account is registered in).
5. On the next screen, confirm the **lock parameters** (see below).

---

## Finding your `user_id` (most important step)

EZVIZ binds remote-unlock permission to an account **user id**. The setup flow
tries to auto-discover it from the lock's user list, but if the field comes up
blank you'll need to supply it. Options, easiest first:

- **Let it auto-fill** — if the second config step pre-populates `user_id`,
  it worked. Just continue.
- **From the CLI** (run on any machine with Python):
  ```bash
  pip install pyezvizapi
  pyezvizapi -u YOUR_EMAIL -p YOUR_PASSWORD -r eu devices status --json
  ```
  Look for the door-lock device and the user/owner id associated with it.
- **EZVIZ app** — the account owner is usually user id `1` on single-owner locks.
  Try `1` first if nothing else is available.

`lock_no` and `local_index` default to `1` and work for most Y2000 units.
`resource_id` defaults to `Video`; **if remote unlock fails, re-add the
integration and set `resource_id` to `DoorLock`.**

---

## Verify before you trust it

Remote unlock parameters vary by firmware, so test once manually:

1. After setup, open **Developer Tools → States** and find `lock.ezviz_y2000_lock`.
2. Stand at the door, then call **`lock.unlock`** on that entity.
3. If the bolt retracts, you're done. If not, check
   **Settings → System → Logs** for `Remote unlock failed` and the HTTP code,
   then adjust `resource_id` / `user_id` / `lock_no` and reconfigure.

Make sure the lock is on the **latest firmware**, has a **valid time zone set**
in the EZVIZ app, and is in **Wi-Fi mode** (not Bluetooth-only) — these are the
common causes of "remote unlock failed" per EZVIZ support.

---

## How lock *state* works (and its limits)

The Y2000 firmware often does **not** emit a "locked" event, and the cloud
status field is unreliable. So the integration **infers** state:

- An unlock command or unlock event → shows **Unlocked**.
- After a ~25 s grace window with no further events → reverts to **Locked**
  (matching the lock's physical auto-relock).

This means the lock state is a near-real-time *best guess*, not a hardware
read-back. Unlocking is a real action; the displayed locked/unlocked state is
inferred. Plan automations accordingly.

---

## Security note

This drives a physical door lock over a reverse-engineered cloud API. Use HA's
2FA, restrict who can call `lock.unlock`, and consider a confirmation step in
any dashboard button. Unofficial — not affiliated with EZVIZ. Use at your own risk.

---

## Credits

- Sensor/listener scaffolding: **iwanstudio** (ha-ezviz-dl03-pro, MIT)
- API library: **RenierM26/pyEzvizApi**
