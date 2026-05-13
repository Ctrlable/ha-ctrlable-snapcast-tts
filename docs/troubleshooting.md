# Troubleshooting

## Add-on won't connect to Snapcast

- Verify `snapcast_host` and `snapcast_rpc_port` in add-on config
- Check that the Snapcast server is running: `systemctl status snapserver`
- The add-on uses default Docker network — no special network config required
- Check add-on logs: **Settings → Add-ons → Ctrlable Snapcast TTS Streamer → Log**

## Add-on doesn't appear in the HA Add-on Store

- Ensure you added the **repository** URL, not the add-on URL
- URL to add: `https://github.com/Ctrlable/ha-ctrlable-snapcast-tts`
- After adding, scroll down in the store — add-ons may take a moment to appear

## HACS integration not found

- Confirm you added the repo with category **Integration**
- Try clearing the HACS cache and refreshing

## TTS plays on the satellite instead of the Snapcast client

- Verify the integration is installed and the satellite has been mapped to a client
- Check HA logs for `ctrlable_snapcast_tts` errors
- Ensure the ESPHome `homeassistant.service` call in `on_intent_progress` is firing (check ESPHome device logs)

## SSH / file-edit mode failing

- Upload the SSH private key in the add-on's Advanced tab
- Ensure the `ssh_user` on the Snapcast host has write access to `snapserver.conf`
- Test: `ssh -i /data/ssh_key <user>@<host> systemctl reload snapserver`

## Client stuck in announcement group after add-on restart

- The watchdog runs on every startup and evicts clients from announcement groups
- Check add-on logs for watchdog output
- Manually run: add-on → **Clients** tab → **Re-detect home group** for the affected client
