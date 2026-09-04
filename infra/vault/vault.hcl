# Vault for the Telegram bot's custodial signer.
#
# This holds the Transit key that wraps every user's private key. Losing it
# loses every custodial wallet, permanently — so storage is persistent (the dev
# stack runs -dev, which is in-memory and would drop them all on a restart) and
# the data directory belongs in the off-site backup.
#
# No TLS on the listener: Vault is published only on the internal Docker
# network, never on a host port, so the only things that can reach it are the
# containers we put there. Terminating TLS in front of it would add a
# certificate to manage without adding a boundary.

ui = false
disable_mlock = false

# /vault/file, not an arbitrary path: the image's entrypoint chowns this
# one to the vault user, and anywhere else Vault cannot write its keyring.
storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = true
}

# Referenced by clients; nothing binds to it externally.
api_addr = "http://vault:8200"
