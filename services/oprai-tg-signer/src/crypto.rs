//! Chain key material: generate / import / sign, for Solana (ed25519) and
//! EVM (secp256k1). Secret bytes are always the 32-byte seed/private key and
//! are wrapped in `Zeroizing` so they are wiped from memory on drop — the whole
//! reason this service is Rust.

use anyhow::{anyhow, bail, Result};
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Chain {
    Solana,
    Evm,
}

impl Chain {
    pub fn parse(s: &str) -> Result<Self> {
        match s.to_ascii_lowercase().as_str() {
            "solana" | "sol" => Ok(Chain::Solana),
            "evm" | "ethereum" | "eth" => Ok(Chain::Evm),
            other => bail!("unknown chain: {other}"),
        }
    }
}

/// A freshly generated or imported keypair: public address + secret bytes.
pub struct KeyMaterial {
    pub address: String,
    pub secret: Zeroizing<Vec<u8>>,
}

/// Generate a new keypair for `chain`.
pub fn generate(chain: Chain) -> Result<KeyMaterial> {
    match chain {
        Chain::Solana => {
            use ed25519_dalek::SigningKey;
            let sk = SigningKey::generate(&mut rand::rngs::OsRng);
            let address = bs58::encode(sk.verifying_key().to_bytes()).into_string();
            Ok(KeyMaterial {
                address,
                secret: Zeroizing::new(sk.to_bytes().to_vec()),
            })
        }
        Chain::Evm => {
            use alloy_signer_local::PrivateKeySigner;
            let signer = PrivateKeySigner::random();
            Ok(KeyMaterial {
                address: signer.address().to_string(),
                secret: Zeroizing::new(signer.to_bytes().to_vec()),
            })
        }
    }
}

/// Import an existing secret (Solana: base58 32/64-byte; EVM: hex 32-byte).
pub fn import(chain: Chain, secret_input: &str) -> Result<KeyMaterial> {
    let s = secret_input.trim();
    match chain {
        Chain::Solana => {
            let raw = bs58::decode(s)
                .into_vec()
                .map_err(|_| anyhow!("invalid base58 secret"))?;
            let seed: [u8; 32] = match raw.len() {
                64 => raw[..32].try_into().unwrap(),
                32 => raw[..32].try_into().unwrap(),
                n => bail!("unexpected solana secret length: {n}"),
            };
            let secret = Zeroizing::new(seed.to_vec());
            let address = address_from_secret(chain, &secret)?;
            Ok(KeyMaterial { address, secret })
        }
        Chain::Evm => {
            let hexs = s.strip_prefix("0x").unwrap_or(s);
            let raw = hex::decode(hexs).map_err(|_| anyhow!("invalid hex secret"))?;
            if raw.len() != 32 {
                bail!("evm secret must be 32 bytes");
            }
            let secret = Zeroizing::new(raw);
            let address = address_from_secret(chain, &secret)?;
            Ok(KeyMaterial { address, secret })
        }
    }
}

/// Recompute the public address from secret bytes (used after Vault decrypt).
pub fn address_from_secret(chain: Chain, secret: &[u8]) -> Result<String> {
    match chain {
        Chain::Solana => {
            use ed25519_dalek::SigningKey;
            let seed: [u8; 32] = secret
                .try_into()
                .map_err(|_| anyhow!("solana seed must be 32 bytes"))?;
            let sk = SigningKey::from_bytes(&seed);
            Ok(bs58::encode(sk.verifying_key().to_bytes()).into_string())
        }
        Chain::Evm => {
            let signer = evm_signer(secret)?;
            Ok(signer.address().to_string())
        }
    }
}

/// Sign a UTF-8 message (used for SIWS/SIWE auth).
/// - Solana: ed25519 over the raw message bytes → base58 signature.
/// - EVM: EIP-191 personal_sign → 0x-hex 65-byte signature.
pub fn sign_message(chain: Chain, secret: &[u8], message: &[u8]) -> Result<String> {
    match chain {
        Chain::Solana => {
            use ed25519_dalek::{Signer, SigningKey};
            let seed: [u8; 32] = secret
                .try_into()
                .map_err(|_| anyhow!("solana seed must be 32 bytes"))?;
            let sk = SigningKey::from_bytes(&seed);
            let sig = sk.sign(message);
            Ok(bs58::encode(sig.to_bytes()).into_string())
        }
        Chain::Evm => {
            use alloy_signer::SignerSync;
            let signer = evm_signer(secret)?;
            let sig = signer
                .sign_message_sync(message)
                .map_err(|e| anyhow!("evm sign_message: {e}"))?;
            Ok(format!("0x{}", hex::encode(sig.as_bytes())))
        }
    }
}

fn evm_signer(secret: &[u8]) -> Result<alloy_signer_local::PrivateKeySigner> {
    use alloy_primitives::B256;
    use alloy_signer_local::PrivateKeySigner;
    let key: [u8; 32] = secret
        .try_into()
        .map_err(|_| anyhow!("evm key must be 32 bytes"))?;
    PrivateKeySigner::from_bytes(&B256::from(key))
        .map_err(|e| anyhow!("evm key parse: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn solana_generate_sign_verify() {
        use ed25519_dalek::{Signature, Verifier, VerifyingKey};
        let km = generate(Chain::Solana).unwrap();
        let pk = bs58::decode(&km.address).into_vec().unwrap();
        assert_eq!(pk.len(), 32, "solana address must be 32-byte pubkey");

        let msg = b"oprai siws challenge";
        let sig_b58 = sign_message(Chain::Solana, &km.secret, msg).unwrap();
        let sig_bytes = bs58::decode(&sig_b58).into_vec().unwrap();
        assert_eq!(sig_bytes.len(), 64);

        let pk_arr: [u8; 32] = pk.as_slice().try_into().unwrap();
        let vk = VerifyingKey::from_bytes(&pk_arr).unwrap();
        let sig = Signature::from_slice(&sig_bytes).unwrap();
        vk.verify(msg, &sig).expect("signature must verify under the pubkey");
    }

    #[test]
    fn solana_import_roundtrip() {
        let km = generate(Chain::Solana).unwrap();
        let secret_b58 = bs58::encode(&*km.secret).into_string();
        let km2 = import(Chain::Solana, &secret_b58).unwrap();
        assert_eq!(km.address, km2.address);
    }

    #[test]
    fn evm_import_known_vector() {
        // Well-known Hardhat/Anvil account #0 — deterministic key→address check.
        let km = import(
            Chain::Evm,
            "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        )
        .unwrap();
        assert_eq!(km.address, "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266");
    }

    #[test]
    fn evm_sign_recovers_address() {
        use alloy_signer::SignerSync;
        let km = generate(Chain::Evm).unwrap();
        let signer = evm_signer(&km.secret).unwrap();
        let msg = b"oprai siwe challenge";
        let sig = signer.sign_message_sync(msg).unwrap();
        let recovered = sig.recover_address_from_msg(msg).unwrap();
        assert_eq!(recovered, signer.address());
    }

    #[test]
    fn chain_parse_aliases() {
        assert_eq!(Chain::parse("sol").unwrap(), Chain::Solana);
        assert_eq!(Chain::parse("ethereum").unwrap(), Chain::Evm);
        assert!(Chain::parse("dogechain").is_err());
    }
}
