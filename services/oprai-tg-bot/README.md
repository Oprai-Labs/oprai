# oprai-tg-bot

OPRAI's Telegram bot — conversational DeFi over Telegram, custodial model.

The bot is a thin frontend over the existing OPRAI stack: it builds intent via
chat-service, builds transactions via the gateway `/actions/*` pipeline, and
hands **unsigned** transactions to the isolated **[oprai-tg-signer](../oprai-tg-signer)**
(Rust) for custody + signing. **The bot holds no private keys.**

- **Language:** Python + [aiogram](https://aiogram.dev) v3 (async).
- **Signer:** Rust (memory-safe key custody + Vault Transit) — separate service.
- **DB:** `tg_schema` in the shared Postgres (`sql/schema.sql`).
- **Auth:** on-behalf SIWS/SIWE — the bot signs the auth nonce through the
  signer, gets a real JWT, and calls the gateway as any client (no gateway
  changes needed).

## Run (dev — long polling)

```bash
cd services/oprai-tg-bot
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
# OPRAI_TELEGRAM_BOT_TOKEN must be set in the repo-root .env (never committed)
.venv/bin/python -m app.main
```

Or via the root Procfile: honcho starts it as `tg-bot`.

## Faz 0 status

- [x] Scaffold, config, structured logging, asyncpg pool
- [x] `tg_schema` DDL (tg_users, tg_wallets, tg_link_tokens, tg_audit)
- [x] `/start`, `/help`
- [ ] 0.3 signer (Rust) — custody + signing + Vault
- [ ] 0.4 auth-on-behalf JWT
- [ ] 0.5 `/wallet`, `/balance`, `/portfolio`
- [ ] 0.7 deep-link account linking

## Security notes

- Bot token is a secret — repo is public; the token lives only in prod `.env`.
- Group/quoted-message content is **untrusted data, never commands**: only the
  initiating user's explicit command triggers an action.
