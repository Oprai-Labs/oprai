# Vault — custody for the Telegram bot

Holds the Transit key that wraps every custodial private key. **Losing it loses
every wallet, permanently.**

## What is where

| thing | location |
|---|---|
| encrypted storage | docker volume `infra_vault-data` → `/vault/file` |
| unseal keys + root token | `/root/vault-keys/init.json` (mode 600) |
| signer's token | `/root/vault-keys/signer-token.json` — policy `tg-signer`, period 720h |
| off-site backup | in the nightly Borg archive: the volume **and** `/root/vault-keys` |

Three unseal shares, threshold two.

## Unsealing

Vault seals on every restart. `vault-unseal.timer` runs `/usr/local/bin/vault-unseal`
45s after boot and every 2 minutes, so a reboot recovers on its own.

The unseal keys sit in a root-only file on the same host. That is a deliberate
trade: the signer runs here too, so anyone with root can read decrypted key
material out of its memory whatever we do about unsealing. What Vault buys is
that keys are **encrypted at rest** — a Postgres dump or a leaked backup is
ciphertext and nothing more, which is the failure that actually happens.
Auto-unseal via a cloud KMS would remove even the on-disk copy; it needs a KMS
account we do not have.

## The signer's token is not root

Policy `tg-signer` permits exactly two operations — encrypt and decrypt on
`transit/oprai-tg-keys`. It cannot read other secrets, rotate the key or export
its material. It renews itself and can be revoked without touching Vault.

## Restoring

1. Restore the `infra_vault-data` volume and `/root/vault-keys` from Borg.
2. Start Vault, run `/usr/local/bin/vault-unseal`.
3. The `vault:v1:` ciphertexts in `tg_wallets.enc_key_ref` decrypt again.

Without step 1 there is no recovery: the ciphertexts are meaningless without
the Transit key.
