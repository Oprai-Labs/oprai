# oprai-tg-signer

Isolated **custodial signer** for the OPRAI Telegram bot. This is the only
component in the Telegram stack that ever touches private keys.

- **Language:** Rust — chosen deliberately for a key-custody service: memory
  safety + `zeroize` to wipe key material from RAM right after signing (a
  GC'd language can't reliably do this).
- **Custody:** private keys are envelope-encrypted with **Vault Transit** (Vault
  holds the KEK; plaintext keys never hit disk). The ciphertext handle lives on
  the signer side; the bot's `tg_wallets` row stores only the public address and
  an opaque `enc_key_ref`.
- **Fail-closed:** if Vault is unreachable, signing is refused (0.3).

## Endpoints (0.3)

| Method | Path             | Purpose                                           |
|--------|------------------|---------------------------------------------------|
| GET    | `/health`        | liveness + Vault status (scaffolded in 0.1)       |
| POST   | `/wallet/create` | generate keypair (solana\|evm), encrypt, store    |
| POST   | `/wallet/import` | import an existing key, encrypt, store            |
| POST   | `/sign`          | decrypt → sign an unsigned tx → wipe → return      |
| POST   | `/siws-sign`     | sign a Sign-In-With-Solana auth message           |
| POST   | `/siwe-sign`     | sign a Sign-In-With-Ethereum (EIP-4361) message   |

## Run (dev)

```bash
cd services/oprai-tg-signer
PORT=3060 cargo run
```

First build downloads crates (a few minutes). The crypto + Vault dependencies
are added in 0.3 (see the commented block in `Cargo.toml`).
