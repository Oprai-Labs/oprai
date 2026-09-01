//! Vault Transit client — envelope encryption for custodial private keys.
//!
//! We never store plaintext keys. A generated/imported private key is encrypted
//! with a Transit key that lives inside Vault (the KEK never leaves Vault); the
//! returned ciphertext (`vault:v1:…`) is the only thing that leaves this process
//! and is what the bot stores as `enc_key_ref`. Decryption requires this
//! service's Vault token, so a leak of the bot DB alone cannot recover keys.

use anyhow::{anyhow, Context, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine};
use serde_json::json;
use zeroize::Zeroizing;

#[derive(Clone)]
pub struct Vault {
    addr: String,
    token: String,
    mount: String,
    key: String,
    http: reqwest::Client,
}

impl Vault {
    /// Build from env. Returns None (fail-closed) if Vault is not configured —
    /// callers must treat "no Vault" as "no signing".
    pub fn from_env() -> Option<Self> {
        let addr = std::env::var("VAULT_ADDR").ok().filter(|s| !s.is_empty())?;
        let token = std::env::var("VAULT_TOKEN").ok().filter(|s| !s.is_empty())?;
        let mount =
            std::env::var("VAULT_TRANSIT_MOUNT").unwrap_or_else(|_| "transit".into());
        let key =
            std::env::var("VAULT_TRANSIT_KEY").unwrap_or_else(|_| "oprai-tg-keys".into());
        Some(Self {
            addr: addr.trim_end_matches('/').to_string(),
            token,
            mount,
            key,
            http: reqwest::Client::new(),
        })
    }

    /// Liveness: is the Transit key reachable? Used by /health.
    pub async fn healthy(&self) -> bool {
        let url = format!("{}/v1/{}/keys/{}", self.addr, self.mount, self.key);
        matches!(
            self.http.get(&url).header("X-Vault-Token", &self.token).send().await,
            Ok(r) if r.status().is_success()
        )
    }

    /// Encrypt raw key bytes → Vault ciphertext string.
    pub async fn encrypt(&self, plaintext: &[u8]) -> Result<String> {
        let url = format!("{}/v1/{}/encrypt/{}", self.addr, self.mount, self.key);
        // Base64 of the key is itself sensitive — wipe our copy on drop. (The
        // serde/reqwest buffers still hold a transient copy en route to Vault;
        // this scrubs the one buffer we own.)
        let b64 = Zeroizing::new(B64.encode(plaintext));
        let resp = self
            .http
            .post(&url)
            .header("X-Vault-Token", &self.token)
            .json(&json!({ "plaintext": &*b64 }))
            .send()
            .await
            .context("vault encrypt request failed")?;
        if !resp.status().is_success() {
            return Err(anyhow!("vault encrypt HTTP {}", resp.status()));
        }
        let v: serde_json::Value = resp.json().await?;
        v["data"]["ciphertext"]
            .as_str()
            .map(str::to_string)
            .ok_or_else(|| anyhow!("vault encrypt: missing ciphertext"))
    }

    /// Decrypt a Vault ciphertext string → raw key bytes.
    pub async fn decrypt(&self, ciphertext: &str) -> Result<Vec<u8>> {
        let url = format!("{}/v1/{}/decrypt/{}", self.addr, self.mount, self.key);
        let resp = self
            .http
            .post(&url)
            .header("X-Vault-Token", &self.token)
            .json(&json!({ "ciphertext": ciphertext }))
            .send()
            .await
            .context("vault decrypt request failed")?;
        if !resp.status().is_success() {
            return Err(anyhow!("vault decrypt HTTP {}", resp.status()));
        }
        let v: serde_json::Value = resp.json().await?;
        let b64 = v["data"]["plaintext"]
            .as_str()
            .ok_or_else(|| anyhow!("vault decrypt: missing plaintext"))?;
        B64.decode(b64).context("vault decrypt: bad base64")
    }
}
