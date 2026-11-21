# Hermes Agent backend

The same bridge can forward Pebble Index 01 recordings to [Hermes Agent](https://github.com/NousResearch/hermes-agent) instead of OpenClaw. The Obsidian capture script is unchanged, and OpenClaw remains the default backend.

## Requirements

- Python 3.11+; no additional Python packages.
- A Hermes release supporting [generic V2 webhook signatures](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks).
- A running Hermes gateway with the webhook platform enabled, and an enabled delivery platform if you want replies in chat.
- The Pebble app must reach the bridge through your tailnet. Do not expose it with Tailscale Funnel.

## 1. Configure a Hermes route

Enable the webhook platform using `hermes gateway setup`. Follow the official webhook documentation for gateway startup and restart requirements. Create a `ring` subscription in the intended Hermes profile:

```shell
hermes webhook subscribe ring \
  --prompt '{message}' \
  --deliver telegram \
  --deliver-chat-id '<your-chat-id>' \
  --mirror-to-session
```

Hermes returns the route URL and its generated secret. Keep the secret private. The default route URL is `http://127.0.0.1:8644/webhooks/ring`. Use the returned URL if your gateway has a different address or profile prefix. Slack and other connected platforms can replace `telegram`; delivery and execution routing are configured in Hermes, not by the Pebble sender.

Subscriptions use the `generic` signature scheme by default. Do not change the route to an unauthenticated or body-only signature scheme. Set `{message}` as the prompt so the transcript is not truncated by the default full-payload template.

**Session behavior differs from OpenClaw:** each Hermes webhook event starts its own run. `--mirror-to-session` adds a successful delivery to the destination chat's existing session, so replies there can have context. The mirror is best-effort and requires an existing chat session; it does not turn separate ring recordings into one persistent conversation.

Before enabling the route, restrict the webhook toolset to the capabilities you intend to expose. For a text-only assistant, the safest starting point is no tools:

```shell
hermes config set platform_toolsets.webhook '[]'
```

This setting applies to all webhook routes in the active profile. If other routes need tools, use a dedicated profile or the documented per-route `toolsets` override instead. Do not disable approvals. A valid signature authenticates the sender, not the transcript's instructions.

## 2. Configure the bridge

Create `~/.config/pebble-index/hook.env` with mode `600`, using a fresh bearer token for the Pebble app and the route secret returned by Hermes:

```dotenv
PEBBLE_HOOK_BACKEND=hermes
PEBBLE_HOOK_TOKEN=<your-pebble-bearer-token>
HERMES_HOOK_SECRET=<your-hermes-route-secret>
HERMES_HOOK_URL=http://127.0.0.1:8644/webhooks/ring
PEBBLE_HOOK_BIND=127.0.0.1:8444
# Recommended: enable request signing in the Pebble app and set the same secret here.
PEBBLE_HOOK_SECRET=<your-pebble-signing-secret>
```

`HERMES_HOOK_URL` is optional when the default above is correct. `OPENCLAW_HOOK_TOKEN` is not needed in Hermes mode. The two signing secrets serve different hops and should not be reused. For a gateway on another host, use an HTTPS URL reachable over the tailnet rather than sending transcripts over public plaintext HTTP.

Install the existing `ops/pebble-hook.service` after changing its checkout path as described in the README, then expose only the bridge through `tailscale serve`. In the Pebble app, keep the same `/index` URL, bearer header, **Double click & hold** gesture and **Transcription** selection as the OpenClaw setup.

## Wire contract and verification

The bridge sends JSON containing only `message` to the configured Hermes URL. It signs the exact outgoing bytes with HMAC-SHA256 over `<unix-seconds>.<body>`, using `X-Webhook-Timestamp` and `X-Webhook-Signature-V2`. It forwards the Pebble delivery ID as `X-Request-ID` for Hermes deduplication. HTTP 200 (including deduplicated events) and HTTP 202 (accepted runs) count as admission success. Neither proves that an agent response was delivered.

Run the local regression and HTTP contract tests without agent credentials:

```shell
python3 -m unittest discover -s tests -v
```

Then, on your own configured gateway:

1. Run `hermes webhook test ring` to check the route and delivery configuration.
2. Use **Send test event** in the Pebble app.
3. Verify the test reply in the configured destination and check both gateway and bridge logs.

The automated tests use local HTTP servers, not a live ring, model, or chat platform. Live delivery requires the manual checks above.

## Limits

This adds an alternate forwarding backend, not a fleet coordinator or durable message queue. Run one active bridge instance. Hermes delivery-ID deduplication is time-limited; the bridge's optional Pebble replay cache is in memory. The existing bridge rejects a repeated signed Pebble delivery even after a downstream failure, so do not assume automatic retries guarantee delivery. Keep the original recording available and inspect failures before resending. No vault writes, gateway configuration changes, services or messaging integrations are performed by selecting the backend in code.
