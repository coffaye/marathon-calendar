# Outlook validation / Outlook 实际订阅验证

Current status:

```text
OUTLOOK_CLIENT_VALIDATION = REQUIRES_HUMAN
```

There is no connected GitHub Pages URL or user-operated Windows 11 New Outlook GUI in this workspace. No client PASS is claimed.

## Minimal user steps

1. Open the new Outlook on Windows 11.
2. Open **Calendar**.
3. Choose **Add calendar**.
4. Choose **Subscribe from web**.
5. Paste the deployed `https://<owner>.github.io/<repo>/calendar/test-subscription.ics` URL.
6. Select **Import** and confirm the V1 event appears.

For the update test, manually run the workflow with `test-feed-v2`. The V2 file must keep the same UID, change `SEQUENCE:0` to `SEQUENCE:1`, change summary/date and update `LAST-MODIFIED`. After Outlook's own refresh interval, confirm the old event was updated rather than leaving both V1 and V2. Record the server publication time, client observation time and latency. This is a pull subscription, not real-time push; several hours without refresh is not by itself a server failure.

The daily `sync` workflow always emits the fixed V1 test event and does not use it as a source or include it in `world.ics`. Do not put a cache-buster in the stable Outlook URL; `?cachebust=<timestamp>` is only a manual Pages/CDN check.

