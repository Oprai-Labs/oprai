//! Raydium CLMM (Concentrated Liquidity Market Maker) — TX builders.
//!
//! Raydium's V3 REST API only exposes swap endpoints — CLMM position
//! lifecycle (open/increase/decrease/close) requires direct Anchor
//! instruction construction against the CLMM program at
//! `CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK`.
//!
//! This module is the bridge: it parses on-chain `PoolState` accounts,
//! derives the position / tick-array PDAs, computes liquidity via the
//! `solana-clmm-raydium` math crate, and encodes the Anchor instructions
//! by hand.
//!
//! Submodules:
//!   - `state` — minimal byte-offset parser for the on-chain `PoolState`.
//!   - `ix`    — Anchor discriminators + instruction data encoders.
//!   - `builder` — top-level `build_open_position` / future close/inc/dec.

pub mod builder;
pub mod ix;
pub mod state;
