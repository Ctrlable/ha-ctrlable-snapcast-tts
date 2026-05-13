# M0 Verification Report

**Date:** 2026-05-13
**Status:** BLOCKED — environment access details not yet provided (see §Environment Access Required below)

---

## §7 M0 Verification Items

### 1. TTS URL format from Piper — PENDING

**Task:** Trigger a long TTS response, capture the URL from `on_intent_progress`, `curl -I` to get Content-Type. Determine if PCM passthrough is viable.

**Status:** Cannot execute — HA TTS engine type and HAOS SSH access not yet provided.

**What to fill in:**
- HA TTS engine in use (Piper local, HA Cloud TTS, etc.)
- Content-Type header from a sample TTS URL
- URL format (e.g., `http://10.1.8.23:8123/api/tts_proxy/...`)
- Whether streaming passthrough is viable from M1

---

### 2. Snapcast version + RPC capabilities — PENDING

**Task:** Connect to RPC port, list streams, confirm whether `Stream.AddStream` is available.

**Status:** Cannot execute — Snapcast server version and SSH access not yet provided.

**What to fill in:**
- `snapserver --version` output
- RPC response from `{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}` on port 1705
- Availability of `Stream.AddStream` method (informational only)

---

### 3. SSH-from-add-on-to-Snapcast-host setup — PENDING

**Task:** Confirm Snapcast runs on `10.1.8.9` LXC, locate `snapserver.conf`, confirm reload mechanism.

**Status:** Cannot execute — SSH key path not yet provided.

**What to verify:**
- `systemctl status snapserver` on `10.1.8.9`
- Location of `snapserver.conf` (typically `/etc/snapserver.conf`)
- `systemctl reload snapserver` vs `systemctl restart snapserver` — which works?
- Write permissions for the user the add-on will SSH as

---

### 4. ESPHome `homeassistant.service` → custom integration service call — PENDING

**Task:** Build minimal throwaway test integration that registers `test.echo`, fire it from ESPHome `homeassistant.service`, confirm it lands.

**Status:** Cannot execute without HAOS access.

**What to verify:**
- Custom integration with `test.echo` service registers successfully
- ESPHome `homeassistant.service` action fires and lands in HA logs
- No auth/encryption issues with the API key

---

## Environment Access Required

The following were listed as `[FILL IN]` in the HANDOFF.md — provide these to proceed:

| Item | Status |
|------|--------|
| HAOS SSH address | Not provided |
| Snapcast LXC SSH key path | Not provided |
| Snapcast version on LXC | Not provided |
| HA TTS engine | Not provided |
| Test satellite device name/IP | Not provided |

Once environment access is established, I will execute the four verification items and update this report.

---

## Blockers

**No blockers confirmed yet** — blockers cannot be assessed without environment access.

Architecture as specified in SPEC.md appears sound. All four verification items are executable once access is provided.

---

## Recommendations (Preliminary)

Based on spec review only:
- PCM passthrough viability depends on Piper output format — likely viable if Piper serves `audio/wav` (PCM with header)
- File-edit mode preconditions depend on SSH access to `10.1.8.9` — verify SSH user has write access to `snapserver.conf` and `systemctl reload` works
- ESPHome → HA service call test is low-risk to pass; the pattern is well-established
