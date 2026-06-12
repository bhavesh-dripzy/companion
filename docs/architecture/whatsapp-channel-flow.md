# WhatsApp channel — end-to-end flow (modelled on OpenClaw)

How OpenClaw supports WhatsApp, and what we'd build to do the same on companion.

> TL;DR — WhatsApp has **no official API for personal numbers**. Everyone (OpenClaw
> included) drives the **WhatsApp Web multi-device protocol** via the
> [Baileys](https://github.com/WhiskeySockets/Baileys) library. It links as a
> *companion device* (the same "Linked devices" feature as web.whatsapp.com), keeps a
> **persistent WebSocket** to WhatsApp servers, and persists an auth blob so it stays
> linked across restarts. Baileys is **Node-only**, so on our Python core it has to run
> as a **long-lived sidecar** that bridges into the pipeline.

---

## 1. How OpenClaw does it (reference)

- One **Gateway** process owns the Baileys socket + reconnect watchdog. CLI / desktop app
  never touch Baileys directly — they talk to the Gateway.
- Login is **QR-based** (`openclaw channels login --channel whatsapp`); a companion device
  is linked via WhatsApp → Settings → Linked devices.
- Auth state lives at `~/.openclaw/credentials/whatsapp/<accountId>/creds.json` (+ `.bak`),
  loaded via Baileys' `useMultiFileAuthState()`.
- Inbound messages are normalised into a shared "inbound envelope", gated by
  `dmPolicy` / `groupPolicy` + allowlists, then routed to an **isolated agent session**
  keyed per chat (`agent:<id>:whatsapp:group:<jid>` etc.).
- Outbound: text chunked at 4000 chars, media transcoded (voice → 48kHz mono Ogg/Opus),
  reactions used as ack/status signals (👀 → thinking → done), one bot-reaction slot per msg.
- Multi-account in one process; `status@broadcast` / newsletters handled specially.

Known Baileys v7 footguns they hit (worth pre-empting):
- `browser` string must look real, e.g. `['Mac OS', 'Chrome', '14.4.1']`.
- `defaultQueryTimeoutMs: undefined` during pairing or login times out prematurely.
- Status code `515` = **restart required** (reconnect with the *same* creds, not an error).
- `DisconnectReason.loggedOut` (401) = creds dead, must re-link with a fresh QR.

---

## 2. The one big adaptation for companion

OpenClaw is all-Node, so Baileys is in-process. **Our core is Python**, and there is no
production-grade Python WhatsApp-Web client. So:

```
WhatsApp servers
      │  (persistent WebSocket, Noise-encrypted)
      ▼
┌─────────────────────────┐
│  wa-sidecar (Node)      │   long-lived process — NOT Lambda
│  • Baileys socket        │   (Baileys needs a held-open socket;
│  • auth state on disk    │    Lambda's request lifecycle can't host it)
│  • QR / pairing-code     │
└───────────┬─────────────┘
   inbound  │  ▲ outbound
   (push)   ▼  │ (command)
┌─────────────────────────┐
│  companion core (Python)│
│  ChannelProvider ─→ Turn │
│  pipeline → publisher    │
└─────────────────────────┘
```

**Infra note:** prod core runs on Lambda; leadgen runs on EC2. The WA sidecar belongs with
the persistent tier (ECS/EC2 container), *not* Lambda — it must hold the socket open 24/7.
One sidecar per linked WhatsApp number; auth blob persisted to a volume / S3 + restored on boot.

Sidecar ↔ core transport (pick one, all low-effort given our stack):
- **Inbound:** sidecar → SQS (reuse the queue pattern) **or** HTTP POST to a `companion_api`
  ingress route. SQS is the better fit — decouples, survives core restarts, matches `boundaries/publisher.py`.
- **Outbound:** core → sidecar HTTP/WS endpoint (`POST /send {jid, text|media, replyTo}`).
  Outbound *must* hit the live sidecar — Baileys can only send from the process holding the socket.

(If we ever want to drop the Node dep, the Go [whatsmeow](https://github.com/tulir/whatsmeow)
lib via a Go sidecar, or its Python wrapper [neonize](https://github.com/krypton-byte/neonize),
are the only real alternatives. Same topology either way.)

---

## 3. The flow, phase by phase

### A. Linking (one-time, per number)
1. Sidecar boots, calls `useMultiFileAuthState(dir)` → `{ state, saveCreds }`.
2. `makeWASocket({ auth: state, browser: ['Mac OS','Chrome','14.4.1'] })`.
3. No creds yet → `connection.update` fires with a `qr` string.
   - **QR path:** render QR (terminal / data-URL to dashboard); user scans via
     WhatsApp → Linked devices.
   - **Pairing-code path (nicer for headless):** `sock.requestPairingCode('<E164 digits, no +>')`
     → show the 8-char code; user enters it in the app. No QR delivery needed.
4. Handshake completes → `connection.update {connection:'open'}`. We're linked.

### B. Auth persistence
- `sock.ev.on('creds.update', saveCreds)` — writes the rotating keys to disk on every update.
- This blob *is* the session. Persist it (volume / S3). Restore on boot → no re-link.
- Never commit it; treat like a credential. Losing it = re-scan QR.

### C. Connection lifecycle / reconnect watchdog
`sock.ev.on('connection.update', ({connection, lastDisconnect, qr}) => …)`:
- `connection:'close'` → inspect `lastDisconnect.error` status code:
  - `515` restart-required → recreate the socket with same creds (normal, happens right after pairing).
  - `loggedOut` (401) → wipe creds, surface "needs re-link", emit fresh QR.
  - everything else (timed out / conn lost / closed) → backoff + reconnect, same creds.
- Watchdog should key off **transport keepalive**, not inbound message volume, so a quiet
  chat doesn't trigger needless restarts (OpenClaw's specific lesson).

### D. Inbound → normalize → Turn
1. `sock.ev.on('messages.upsert', ({messages, type}) => …)`; ignore `type !== 'notify'`,
   skip `fromMe`, skip `status@broadcast`.
2. Extract from `msg.key` + `msg.message`:
   - `jid = msg.key.remoteJid` → `…@s.whatsapp.net` (DM) or `…@g.us` (group).
   - sender (in groups: `msg.key.participant`), text (`conversation` / `extendedTextMessage.text`),
     quoted/reply context, media nodes (image/audio/video/document/location/contact).
3. **Media:** download via Baileys (`downloadMediaMessage`), drop a placeholder
   (`<media:image>`, transcribe voice notes), push bytes to S3 → our `contracts/attachment.py`.
4. Normalise to a channel-neutral inbound envelope, ship to core (SQS/HTTP).
5. Core's **ChannelProvider** turns the envelope into a `core/contracts/turn.py` Turn:
   - resolve/attach `Session` keyed per chat — e.g. `whatsapp:dm:<jid>` /
     `whatsapp:group:<jid>` (mirror OpenClaw's session isolation).
   - `phone` field already exists on `TurnEvent`/profile — reuse it as identity.

### E. Access control (before we spend a model call)
- DMs: `dmPolicy ∈ {pairing, allowlist, open, disabled}` + `allowFrom` (E.164).
  `pairing` = unknown sender gets a short code to register (OpenClaw default; anti-spam).
- Groups: must be in `groups` allowlist; sender vs `groupPolicy`; **respond only on mention**
  (explicit @, regex, reply-to-bot, or voice transcript match) — otherwise buffer to pending history.

### F. Pipeline → outbound
1. Normalised Turn runs the **existing** `core/pipeline` (routing → provider → LLM). No WA-specific logic here.
2. On reply, optionally emit ack/status **reactions** through the sidecar
   (👀 received → 🧠 thinking → ✅ done) — one reaction slot per message.
3. Final text → core sends an outbound command to the sidecar:
   - chunk at ~4000 chars (prefer newline boundaries);
   - `sock.sendMessage(jid, { text }, { quoted })` for replies;
   - media: `{ image: buf, caption }`, voice `{ audio: buf, ptt: true, mimetype:'audio/ogg; codecs=opus' }`;
   - group mentions: attach `mentions: [jid]` matching `@<digits>` tokens.
4. `sock.readMessages([key])` for read receipts (configurable).
5. Outbound also feeds the normal `boundaries/publisher.py` → `TurnEvent` so the KG/leadgen
   seam sees WhatsApp turns like any other.

---

## 4. Mapping onto companion's existing seams

| WhatsApp concept            | companion home                                           |
|-----------------------------|----------------------------------------------------------|
| Channel adapter / ingress   | new `core/channels/` (mirror `core/providers/` shape) — a `ChannelProvider` ABC in `contracts/` |
| Inbound envelope → Turn     | `core/contracts/turn.py`, `turn_event.py`                |
| Per-chat session            | `core/contracts/session.py` (`whatsapp:dm\|group:<jid>`) |
| Identity                    | `phone` already on `TurnEvent` / `contracts/profile.py`  |
| Attachments                 | `core/contracts/attachment.py` (+ S3)                    |
| Outbound rendering          | new sidecar client behind a `boundaries/protocols.py` Protocol |
| KG/leadgen fan-out          | unchanged — `boundaries/publisher.py`                    |
| Transport in/out            | SQS (reuse) inbound; HTTP/WS to sidecar outbound         |

The pattern is the inverse of `McpProvider`: MCP providers are *egress* (tools the agent
calls); a `ChannelProvider` is *ingress* (surfaces that produce Turns). Same registry idea,
opposite direction.

---

## 5. Build milestones (suggested)
1. **wa-sidecar** (Node + Baileys): link via pairing code, persist auth, log inbound, expose `POST /send`. Run locally first.
2. **Bridge:** sidecar → SQS inbound; core `ChannelProvider` consumes → builds a Turn → runs pipeline → calls `/send`. Echo bot end-to-end.
3. **Normalisation:** media download→S3→attachment, voice transcription, quoted/reply context, group mention gating.
4. **Access control:** `dmPolicy`/`allowFrom`, pairing codes, group allowlist.
5. **Polish:** status reactions, chunking, read receipts, reconnect/`515`/`loggedOut` handling, auth-blob backup to S3.
6. **Deploy:** sidecar to the persistent tier (ECS/EC2), auth volume, health + relink alerting.

---

## 6. Risks
- **Unofficial protocol** — Baileys can break on WhatsApp updates; pin a version, watch upstream.
- **Ban risk** — WhatsApp may flag automation, especially bulk/cold outbound. Use a dedicated
  number, keep volume human, only reply to inbound, respect pairing/allowlist.
- **Single socket** — one number = one live process; HA needs careful session-blob handoff, not two sockets.
- **Not Lambda** — the held-open WebSocket forces a persistent host for the sidecar.

## References
- OpenClaw WhatsApp docs — https://docs.openclaw.ai/channels/whatsapp
- OpenClaw channels overview — https://docs.openclaw.ai/channels
- Baileys — https://github.com/WhiskeySockets/Baileys
- whatsmeow (Go alt) — https://github.com/tulir/whatsmeow · neonize (Python wrapper) — https://github.com/krypton-byte/neonize
