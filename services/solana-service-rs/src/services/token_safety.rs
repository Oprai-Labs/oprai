//! What can be checked about a token before someone spends money on it.
//!
//! The standard this is built to: a user with no DeFi knowledge must not be
//! able to lose money to a token whose danger was detectable, without having
//! been stopped and told why in plain language.
//!
//! Three decisions shape the whole module.
//!
//! **The mint account is the source of truth.** Measured on 2026-08-01: a
//! token minted minutes earlier returned three populated fields from Birdeye
//! and complete authority data from `getAccountInfo`. Scams live in exactly
//! that window — the first hour, before any indexer has caught up. An indexer
//! that is blind when it matters is not a safety layer, so it enriches and
//! does not decide.
//!
//! **Severity follows capability, not novelty.** Every pump.fun token is
//! unverified, new and thinly traded. If those gate a transaction then every
//! memecoin gates one, users learn to click through, and the one with a
//! permanent delegate goes past unread. Only findings that describe someone
//! else being able to move or trap the user's money are allowed to stop them.
//!
//! **A finding names its consequence.** "freezeAuthority: 7xKq…" is not a
//! warning. "The issuer can freeze your tokens, so you may not be able to
//! sell" is.

use serde::{Deserialize, Serialize};
use std::str::FromStr;

use crate::error::AppError;
use crate::solana::connection::SolanaRpc;

/// How strongly a finding should interrupt.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Severity {
    /// Context. Shown, never gates.
    Note,
    /// A real cost or risk to weigh. Deliberate confirmation.
    Warn,
    /// Built so someone else can take or trap the money. Gated.
    Block,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Finding {
    pub severity: Severity,
    /// A short name for the thing found.
    pub title: String,
    /// What it means for the person about to spend, in one sentence.
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenSafety {
    pub mint: String,
    pub severity: Severity,
    pub findings: Vec<Finding>,
    /// True when the checks ran and found nothing that can take the money.
    /// NOT a statement that the token is safe — see `limits`.
    pub clean: bool,
    /// What this check cannot see, carried with the answer so nobody builds
    /// confidence on top of the gap.
    pub limits: Vec<String>,
    pub token2022: bool,
    pub verified: Option<bool>,
}

const SPL_TOKEN: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const SPL_TOKEN_2022: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

fn limits() -> Vec<String> {
    vec![
        "Liquidity can still be removed after you buy".into(),
        "A team can abandon a project that looks healthy".into(),
        "Claims made about a token off-chain are not checked here".into(),
    ]
}

/// Read the facts that decide whether money can be taken, straight from the
/// mint account. Always current, and available the second a token exists.
pub async fn inspect_mint(rpc: &SolanaRpc, mint: &str) -> Result<TokenSafety, AppError> {
    let pubkey = solana_sdk::pubkey::Pubkey::from_str(mint)
        .map_err(|_| AppError::InvalidParams("That is not a token address".into()))?;

    // The RPC client is blocking and Actix workers are single-threaded — the
    // established pattern here is to hand it to a blocking thread rather than
    // panic the worker.
    let rpc2 = rpc.clone();
    let account = tokio::task::spawn_blocking(move || rpc2.client().get_account(&pubkey))
        .await
        .map_err(|e| AppError::Internal(format!("token account read failed: {e}")))?
        .map_err(|_| AppError::NotFound("No token exists at that address".into()))?;

    let owner = account.owner.to_string();
    if owner != SPL_TOKEN && owner != SPL_TOKEN_2022 {
        return Err(AppError::InvalidParams(
            "That address is not a token — it may be a wallet or a program".into(),
        ));
    }
    let token2022 = owner == SPL_TOKEN_2022;

    let mut findings: Vec<Finding> = Vec::new();

    // The base mint layout is the first 82 bytes in both programs.
    //   0..4   mint_authority option tag
    //   4..36  mint_authority
    //   36..44 supply
    //   44     decimals
    //   45     is_initialized
    //   46..50 freeze_authority option tag
    //   50..82 freeze_authority
    let data = &account.data;
    if data.len() < 82 {
        return Err(AppError::ProtocolError(
            "That token's account could not be read".into(),
        ));
    }
    let has_mint_authority = u32::from_le_bytes([data[0], data[1], data[2], data[3]]) == 1;
    let has_freeze_authority = u32::from_le_bytes([data[46], data[47], data[48], data[49]]) == 1;

    if has_freeze_authority {
        findings.push(Finding {
            severity: Severity::Block,
            title: "The issuer can freeze your tokens".into(),
            detail: "Whoever controls this token can freeze your wallet's balance at any time. \
                     If they do, you will not be able to sell — this is the most common way \
                     buyers are trapped."
                .into(),
        });
    }
    if has_mint_authority {
        findings.push(Finding {
            severity: Severity::Warn,
            title: "More tokens can still be created".into(),
            detail: "The supply is not fixed. Whoever controls this token can mint more at any \
                     time, which dilutes what you hold."
                .into(),
        });
    }

    // Token-2022 extensions live past the base layout, after a one-byte
    // account type at offset 165. Each is a two-byte type, a two-byte length,
    // then its data.
    if token2022 && data.len() > 166 {
        let mut i = 166usize;
        while i + 4 <= data.len() {
            let ext_type = u16::from_le_bytes([data[i], data[i + 1]]);
            let len = u16::from_le_bytes([data[i + 2], data[i + 3]]) as usize;
            let body = i + 4;
            if body + len > data.len() {
                break;
            }
            match ext_type {
                // TransferFeeConfig — the tax, and it is charged on every
                // transfer including the one that sells.
                1 => {
                    // newer_transfer_fee sits at the end of the struct:
                    // epoch(8) + maximum_fee(8) + basis_points(2)
                    if len >= 18 {
                        let bp_at = body + len - 2;
                        let bps = u16::from_le_bytes([data[bp_at], data[bp_at + 1]]);
                        if bps > 0 {
                            findings.push(Finding {
                                severity: if bps >= 1000 { Severity::Block } else { Severity::Warn },
                                title: format!("{}% tax on every transfer", bps as f64 / 100.0),
                                detail: format!(
                                    "This token charges {}% each time it moves, including when \
                                     you sell. Buying and selling costs you {}% before any price \
                                     change.",
                                    bps as f64 / 100.0,
                                    (bps as f64 / 100.0) * 2.0
                                ),
                            });
                        }
                    }
                }
                // NonTransferable
                13 => findings.push(Finding {
                    severity: Severity::Block,
                    title: "This token cannot be sold".into(),
                    detail: "It is marked non-transferable on chain. Once bought it cannot be \
                             moved or sold by anyone, including you."
                        .into(),
                }),
                // PermanentDelegate
                12 => findings.push(Finding {
                    severity: Severity::Block,
                    title: "Someone else can take these tokens".into(),
                    detail: "This token has a permanent delegate: a fixed address that can move \
                             or burn your balance at any time without asking you."
                        .into(),
                }),
                // DefaultAccountState — new accounts can start frozen.
                7 => {
                    if len >= 1 && data[body] == 2 {
                        findings.push(Finding {
                            severity: Severity::Block,
                            title: "New balances start frozen".into(),
                            detail: "Accounts for this token are created in a frozen state, so \
                                     what you buy cannot be sold unless the issuer unfreezes it."
                                .into(),
                        });
                    }
                }
                _ => {}
            }
            // Extensions are 4-byte aligned in practice; step to the next one.
            i = body + len;
            if len == 0 {
                break;
            }
        }
    }

    let severity = findings
        .iter()
        .map(|f| f.severity)
        .max()
        .unwrap_or(Severity::Note);

    Ok(TokenSafety {
        mint: mint.to_string(),
        clean: findings.iter().all(|f| f.severity == Severity::Note),
        severity,
        findings,
        limits: limits(),
        token2022,
        verified: None,
    })
}

/// Add what the indexers know: how concentrated the holders are, whether
/// anyone has verified the token, how thin the market is. None of it can take
/// the user's money on its own, so none of it gates.
pub async fn enrich(http: &reqwest::Client, safety: &mut TokenSafety) {
    let mint = safety.mint.clone();

    let shield = async {
        let url = format!("https://lite-api.jup.ag/ultra/v1/shield?mints={mint}");
        http.get(&url).send().await.ok()?.json::<serde_json::Value>().await.ok()
    };
    let birdeye = async {
        let key = std::env::var("BIRDEYE_API_KEY").ok().filter(|k| !k.is_empty())?;
        let url = format!("https://public-api.birdeye.so/defi/token_security?address={mint}");
        http.get(&url)
            .header("X-API-KEY", key)
            .header("x-chain", "solana")
            .send()
            .await
            .ok()?
            .json::<serde_json::Value>()
            .await
            .ok()
    };
    let (shield, birdeye) = futures::join!(shield, birdeye);

    if let Some(v) = shield {
        if let Some(list) = v.pointer(&format!("/warnings/{mint}")).and_then(|w| w.as_array()) {
            let types: Vec<&str> = list
                .iter()
                .filter_map(|w| w.get("type").and_then(|t| t.as_str()))
                .collect();
            let verified = !types.contains(&"NOT_VERIFIED");
            safety.verified = Some(verified);

            // The same fact means different things depending on who holds it.
            //
            // USDC has a freeze authority and a mint authority: Circle can
            // freeze an account and can issue more, both disclosed, both the
            // reason a regulated stablecoin works. Graded on the fact alone,
            // this check called USDC and JLP dangerous — which is how a
            // safety feature loses its readers, because the first thing it
            // says is obviously wrong.
            //
            // On a verified token these are properties of a known issuer, so
            // they are stated rather than used to stop anyone. On an
            // unverified one nobody knows who holds them, and they stay a
            // block.
            if verified {
                for f in safety.findings.iter_mut() {
                    if f.title.starts_with("The issuer can freeze") {
                        f.severity = Severity::Note;
                        f.detail = "This token's issuer can freeze balances. On a known issuer \
                                    that is a disclosed feature — it is how a regulated \
                                    stablecoin can reverse a theft — but it is still a power \
                                    they hold over your balance."
                            .into();
                    } else if f.title.starts_with("More tokens can still") {
                        f.severity = Severity::Note;
                        f.detail = "This token's issuer can create more of it. For a stablecoin \
                                    or a liquidity token that is how it works; the supply tracks \
                                    what backs it."
                            .into();
                    }
                }
            }
            if types.contains(&"NOT_VERIFIED") {
                safety.findings.push(Finding {
                    severity: Severity::Note,
                    title: "Not on the verified token list".into(),
                    detail: "Anyone can create a token with any name. Being unlisted is normal \
                             for something new — check the address matches the one you meant."
                        .into(),
                });
            }
            if types.contains(&"LOW_LIQUIDITY") {
                safety.findings.push(Finding {
                    severity: Severity::Note,
                    title: "Very little liquidity".into(),
                    detail: "There is not much to trade against, so selling even a small amount \
                             can move the price a long way against you."
                        .into(),
                });
            }
        }
    }

    if let Some(v) = birdeye {
        let d = v.get("data");
        let pct = |k: &str| d.and_then(|x| x.get(k)).and_then(|x| x.as_f64());
        if let Some(top10) = pct("top10HolderPercent") {
            if top10 > 0.8 {
                safety.findings.push(Finding {
                    severity: Severity::Warn,
                    title: format!("{}% held by ten wallets", (top10 * 100.0).round()),
                    detail: "A handful of holders own almost all of it. Any one of them selling \
                             can take the price down before you can react."
                        .into(),
                });
            }
        }
        if let Some(creator) = pct("creatorPercentage") {
            if creator > 0.2 {
                safety.findings.push(Finding {
                    severity: Severity::Warn,
                    title: format!("Creator holds {}%", (creator * 100.0).round()),
                    detail: "The person who made this token still owns a large share of it."
                        .into(),
                });
            }
        }
        if d.and_then(|x| x.get("mutableMetadata")).and_then(|x| x.as_bool()) == Some(true) {
            safety.findings.push(Finding {
                severity: Severity::Note,
                title: "Name and image can be changed".into(),
                detail: "The metadata is mutable, so what this token calls itself today is not \
                         guaranteed to be what it calls itself tomorrow."
                    .into(),
            });
        }
    }

    safety.severity = safety
        .findings
        .iter()
        .map(|f| f.severity)
        .max()
        .unwrap_or(Severity::Note);
    safety.clean = safety.findings.iter().all(|f| f.severity == Severity::Note);
}

/// Params for the `token_safety` query.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenSafetyParams {
    #[serde(alias = "mint", alias = "address", alias = "token")]
    pub mint_address: String,
}
