//! OPRAI's name, attached to any transaction we hand out.
//!
//! Every builder in this service ends at `build_action`, so the memo is added
//! there rather than in forty places — most of which build their transaction
//! through someone else's SDK or API and never assemble instructions of their
//! own.
//!
//! That means operating on a finished, serialised transaction, which is only
//! safe under strict conditions. All of them are checked below, and anything
//! uncertain returns the transaction untouched: a name on a transaction is
//! never worth corrupting one.

use base64::Engine;
use solana_sdk::instruction::CompiledInstruction;
use solana_sdk::message::VersionedMessage;
use solana_sdk::pubkey::Pubkey;
use solana_sdk::signature::Signature;
use solana_sdk::transaction::VersionedTransaction;
use std::str::FromStr;

pub const MEMO_PROGRAM: &str = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";

/// Short on purpose. Every byte is one the transaction cannot use, and the
/// tightest paths here sit within a few dozen bytes of the limit.
pub const MEMO_TEXT: &[u8] = b"OPRAI";

pub const MAX_TX_BYTES: usize = 1232;

fn memo_program() -> Pubkey {
    Pubkey::from_str(MEMO_PROGRAM).expect("valid memo program id")
}

/// Attach the memo to a base64 transaction, or return it exactly as given.
///
/// Returns the input unchanged when the transaction is already signed, already
/// carries a memo, cannot be parsed, or would exceed the size limit. Those are
/// not failures worth reporting — the transaction is still perfectly good,
/// just anonymous.
pub fn attach(tx_base64: &str) -> String {
    match try_attach(tx_base64) {
        Some(updated) => updated,
        None => tx_base64.to_string(),
    }
}

/// Where a lookup table already places `wanted`, in the message's index space.
///
/// Resolved order is: static keys, then every table's writable entries in
/// table order, then every table's readonly entries. Reading the tables costs
/// one account fetch each, cached for the life of the process — a table's
/// contents only ever grow, and an address that is in one stays in it.
fn lookup_index_of(wanted: &Pubkey, m: &solana_sdk::message::v0::Message) -> Option<u8> {
    if m.address_table_lookups.is_empty() {
        return None;
    }
    let statics = m.account_keys.len();
    let tables: Vec<Vec<Pubkey>> = m
        .address_table_lookups
        .iter()
        .map(|l| read_lookup_table(&l.account_key))
        .collect();

    let writable_total: usize = m
        .address_table_lookups
        .iter()
        .map(|l| l.writable_indexes.len())
        .sum();

    let mut seen = 0usize;
    for (lookup, addrs) in m.address_table_lookups.iter().zip(tables.iter()) {
        for (n, idx) in lookup.writable_indexes.iter().enumerate() {
            if addrs.get(*idx as usize) == Some(wanted) {
                return u8::try_from(statics + seen + n).ok();
            }
        }
        seen += lookup.writable_indexes.len();
    }
    let mut seen = 0usize;
    for (lookup, addrs) in m.address_table_lookups.iter().zip(tables.iter()) {
        for (n, idx) in lookup.readonly_indexes.iter().enumerate() {
            if addrs.get(*idx as usize) == Some(wanted) {
                return u8::try_from(statics + writable_total + seen + n).ok();
            }
        }
        seen += lookup.readonly_indexes.len();
    }
    None
}

/// The addresses a lookup table holds. Cached; tables only grow.
fn read_lookup_table(key: &Pubkey) -> Vec<Pubkey> {
    use std::sync::{Mutex, OnceLock};
    static CACHE: OnceLock<Mutex<std::collections::HashMap<Pubkey, Vec<Pubkey>>>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(std::collections::HashMap::new()));
    if let Some(hit) = cache.lock().ok().and_then(|c| c.get(key).cloned()) {
        return hit;
    }
    let endpoint = std::env::var("SOLANA_RPC")
        .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".to_string());
    let addrs = crate::solana::connection::SolanaRpc::new(&endpoint)
        .client()
        .get_account(key)
        .ok()
        .map(|acc| {
            const HEADER: usize = 56;
            acc.data
                .get(HEADER..)
                .map(|rest| {
                    rest.chunks_exact(32)
                        .filter_map(|c| Pubkey::try_from(c).ok())
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default()
        })
        .unwrap_or_default();
    if let Ok(mut c) = cache.lock() {
        c.insert(*key, addrs.clone());
    }
    addrs
}

/// Append a plain SOL transfer to an unsigned transaction.
///
/// This is how a fee gets taken without depending on anything existing first.
/// Jupiter's own platform fee needs a token account that Jupiter will not
/// create, which left every SOL-side swap uncharged until someone happened to
/// make a pump.fun trade — a commission that depended on an unrelated action
/// is not a commission. A transfer needs no account: native SOL is the balance
/// itself.
///
/// Returns the transaction unchanged if it is already signed, cannot be
/// parsed, or would exceed the size limit.
pub fn attach_sol_transfer(tx_base64: &str, from: &Pubkey, to: &Pubkey, lamports: u64) -> String {
    if lamports == 0 {
        return tx_base64.to_string();
    }
    match try_attach_transfer(tx_base64, from, to, lamports) {
        Some(updated) => updated,
        None => tx_base64.to_string(),
    }
}

fn try_attach_transfer(
    tx_base64: &str,
    from: &Pubkey,
    to: &Pubkey,
    lamports: u64,
) -> Option<String> {
    let bytes = base64::engine::general_purpose::STANDARD.decode(tx_base64).ok()?;
    let mut tx: VersionedTransaction = bincode::deserialize(&bytes).ok()?;
    if tx.signatures.iter().any(|s| *s != Signature::default()) {
        return None;
    }

    let system = solana_sdk::system_program::id();
    let mut data = Vec::with_capacity(12);
    data.extend_from_slice(&2u32.to_le_bytes()); // SystemInstruction::Transfer
    data.extend_from_slice(&lamports.to_le_bytes());

    let (keys, header) = match &mut tx.message {
        VersionedMessage::Legacy(m) => (&mut m.account_keys, &mut m.header),
        VersionedMessage::V0(m) => (&mut m.account_keys, &mut m.header),
    };
    let from_idx = u8::try_from(keys.iter().position(|k| k == from)?).ok()?;

    // The recipient must be WRITABLE, and a writable non-signer does not live
    // at the end of the key list — it lives before the readonly ones. So it is
    // inserted at that boundary, and every index at or past it moves along by
    // one, lookup-table accounts included. Appending it at the end instead
    // would silently make it read-only and the transfer would fail.
    let insert_at = keys.len() - header.num_readonly_unsigned_accounts as usize;
    let to_idx = match keys.iter().position(|k| k == to) {
        Some(existing) => u8::try_from(existing).ok()?,
        None => {
            let at = u8::try_from(insert_at).ok()?;
            shift_indices_from(&mut tx, insert_at)?;
            match &mut tx.message {
                VersionedMessage::Legacy(m) => m.account_keys.insert(insert_at, *to),
                VersionedMessage::V0(m) => m.account_keys.insert(insert_at, *to),
            }
            at
        }
    };

    // The system program is read-only, so it goes on the end of the static
    // keys — but "the end" is not free either: the lookup-table accounts are
    // numbered immediately after the static ones, so appending still pushes
    // every one of them along. A test caught exactly this: the swap's own
    // accounts came back renumbered by one.
    let existing_sys = match &tx.message {
        VersionedMessage::Legacy(m) => m.account_keys.iter().position(|k| *k == system),
        VersionedMessage::V0(m) => m.account_keys.iter().position(|k| *k == system),
    };
    let sys_idx = match existing_sys {
        Some(existing) => u8::try_from(existing).ok()?,
        None => {
            let at_usize = match &tx.message {
                VersionedMessage::Legacy(m) => m.account_keys.len(),
                VersionedMessage::V0(m) => m.account_keys.len(),
            };
            let at = u8::try_from(at_usize).ok()?;
            shift_indices_from(&mut tx, at_usize)?;
            match &mut tx.message {
                VersionedMessage::Legacy(m) => {
                    m.account_keys.push(system);
                    m.header.num_readonly_unsigned_accounts =
                        m.header.num_readonly_unsigned_accounts.checked_add(1)?;
                }
                VersionedMessage::V0(m) => {
                    m.account_keys.push(system);
                    m.header.num_readonly_unsigned_accounts =
                        m.header.num_readonly_unsigned_accounts.checked_add(1)?;
                }
            }
            at
        }
    };

    let ix = CompiledInstruction {
        program_id_index: sys_idx,
        accounts: vec![from_idx, to_idx],
        data,
    };
    match &mut tx.message {
        VersionedMessage::Legacy(m) => m.instructions.push(ix),
        VersionedMessage::V0(m) => m.instructions.push(ix),
    }

    let required = tx.message.header().num_required_signatures as usize;
    if tx.signatures.len() != required {
        return None;
    }
    let out = bincode::serialize(&tx).ok()?;
    if out.len() > MAX_TX_BYTES {
        tracing::warn!(bytes = out.len(), "no room for the fee transfer — trade goes uncharged");
        return None;
    }
    Some(base64::engine::general_purpose::STANDARD.encode(&out))
}

/// Move every account index at or past `from` along by one.
///
/// A message addresses its static keys and its lookup-table accounts in one
/// continuous space, so inserting a static key renumbers everything after it.
/// Getting this wrong does not fail loudly — the transaction builds, signs,
/// and touches the wrong accounts.
fn shift_indices_from(tx: &mut VersionedTransaction, from: usize) -> Option<()> {
    let instructions = match &mut tx.message {
        VersionedMessage::Legacy(m) => &mut m.instructions,
        VersionedMessage::V0(m) => &mut m.instructions,
    };
    for ix in instructions.iter_mut() {
        if ix.program_id_index as usize >= from {
            ix.program_id_index = ix.program_id_index.checked_add(1)?;
        }
        for a in ix.accounts.iter_mut() {
            if *a as usize >= from {
                *a = a.checked_add(1)?;
            }
        }
    }
    Some(())
}

fn try_attach(tx_base64: &str) -> Option<String> {
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(tx_base64)
        .ok()?;
    let mut tx: VersionedTransaction = bincode::deserialize(&bytes).ok()?;

    // A signature covers the message. Adding an instruction changes the
    // message, so every existing signature becomes invalid — and some of
    // these transactions are co-signed by someone we cannot ask again
    // (Magic Eden signs listings before we ever see them).
    if tx.signatures.iter().any(|s| *s != Signature::default()) {
        return None;
    }

    let memo = memo_program();
    let already_present = match &tx.message {
        VersionedMessage::Legacy(m) => m.account_keys.contains(&memo),
        VersionedMessage::V0(m) => m.account_keys.contains(&memo),
    };
    if already_present {
        return None;
    }

    // A v0 message can reach an account through a lookup table without listing
    // it statically — and Jupiter's tables contain the memo program. Adding it
    // as a static key then names the same account twice, which the runtime
    // rejects outright with AccountLoadedTwice. Whether it happens depends on
    // the route, so it broke swaps intermittently and passed every test that
    // took the other path.
    //
    // When the table already offers it, use the index the table gives.
    if let VersionedMessage::V0(m) = &tx.message {
        if let Some(index) = lookup_index_of(&memo, m) {
            let ix = CompiledInstruction {
                program_id_index: index,
                accounts: vec![],
                data: MEMO_TEXT.to_vec(),
            };
            let mut tx = tx;
            if let VersionedMessage::V0(m) = &mut tx.message {
                m.instructions.push(ix);
            }
            let out = bincode::serialize(&tx).ok()?;
            if out.len() > MAX_TX_BYTES {
                return None;
            }
            return Some(base64::engine::general_purpose::STANDARD.encode(&out));
        }
    }

    match &mut tx.message {
        VersionedMessage::Legacy(m) => {
            // Legacy messages address only their own key list, so appending at
            // the end shifts nothing. The end is also the readonly-unsigned
            // region, which is what a memo program should be.
            let index = u8::try_from(m.account_keys.len()).ok()?;
            m.account_keys.push(memo);
            m.header.num_readonly_unsigned_accounts =
                m.header.num_readonly_unsigned_accounts.checked_add(1)?;
            m.instructions.push(CompiledInstruction {
                program_id_index: index,
                accounts: vec![],
                data: MEMO_TEXT.to_vec(),
            });
        }
        VersionedMessage::V0(m) => {
            // A v0 message addresses its static keys first and its lookup-table
            // accounts after them, in one continuous index space. Appending a
            // static key therefore pushes every table account along by one, and
            // every instruction that referenced one now points at its
            // neighbour. Getting this wrong does not fail loudly — it builds a
            // transaction that touches the wrong accounts.
            let old_static_len = m.account_keys.len();
            let index = u8::try_from(old_static_len).ok()?;
            for ix in m.instructions.iter_mut() {
                if ix.program_id_index as usize >= old_static_len {
                    ix.program_id_index = ix.program_id_index.checked_add(1)?;
                }
                for a in ix.accounts.iter_mut() {
                    if *a as usize >= old_static_len {
                        *a = a.checked_add(1)?;
                    }
                }
            }
            m.account_keys.push(memo);
            m.header.num_readonly_unsigned_accounts =
                m.header.num_readonly_unsigned_accounts.checked_add(1)?;
            m.instructions.push(CompiledInstruction {
                program_id_index: index,
                accounts: vec![],
                data: MEMO_TEXT.to_vec(),
            });
        }
    }

    // The memo is a non-signer, so the signature count must not have moved.
    // If it did, something about this transaction is not what we assumed.
    let required = tx.message.header().num_required_signatures as usize;
    if tx.signatures.len() != required {
        return None;
    }

    let out = bincode::serialize(&tx).ok()?;
    if out.len() > MAX_TX_BYTES {
        tracing::debug!(bytes = out.len(), "no room for the OPRAI memo — leaving it off");
        return None;
    }
    Some(base64::engine::general_purpose::STANDARD.encode(&out))
}

#[cfg(test)]
mod tests {
    use super::*;
    use solana_sdk::hash::Hash;
    use solana_sdk::instruction::Instruction;
    use solana_sdk::message::{v0, AddressLookupTableAccount, Message};
    use solana_sdk::signature::{Keypair, Signer};
    use solana_sdk::system_instruction;
    use solana_sdk::transaction::Transaction;

    fn encode(tx: &VersionedTransaction) -> String {
        base64::engine::general_purpose::STANDARD.encode(bincode::serialize(tx).unwrap())
    }
    fn decode(b64: &str) -> VersionedTransaction {
        bincode::deserialize(&base64::engine::general_purpose::STANDARD.decode(b64).unwrap())
            .unwrap()
    }

    #[test]
    fn legacy_transaction_gains_the_memo() {
        let payer = Pubkey::new_unique();
        let to = Pubkey::new_unique();
        let msg = Message::new(&[system_instruction::transfer(&payer, &to, 5)], Some(&payer));
        let tx: VersionedTransaction = Transaction::new_unsigned(msg).into();
        let before = tx.message.instructions().len();

        let out = decode(&attach(&encode(&tx)));
        assert_eq!(out.message.instructions().len(), before + 1);
        assert!(out.message.static_account_keys().contains(&memo_program()));
    }

    /// The one that matters: a v0 transaction's lookup-table accounts sit after
    /// its static keys in a single index space, so adding a static key moves
    /// them all. If the indices are not corrected the transaction still builds
    /// and still signs — it just touches the wrong accounts.
    #[test]
    fn v0_lookup_table_accounts_still_resolve_after_the_memo() {
        let payer = Keypair::new();
        let in_table: Vec<Pubkey> = (0..4).map(|_| Pubkey::new_unique()).collect();
        let lut = AddressLookupTableAccount {
            key: Pubkey::new_unique(),
            addresses: in_table.clone(),
        };
        let program = Pubkey::new_unique();
        // Touch two accounts that only exist in the lookup table.
        let ix = Instruction::new_with_bytes(
            program,
            &[1, 2, 3],
            vec![
                solana_sdk::instruction::AccountMeta::new(in_table[1], false),
                solana_sdk::instruction::AccountMeta::new_readonly(in_table[3], false),
            ],
        );
        let msg = v0::Message::try_compile(&payer.pubkey(), &[ix], &[lut.clone()], Hash::default())
            .expect("compiles");
        let tx = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::V0(msg),
        };

        let out = decode(&attach(&encode(&tx)));
        let VersionedMessage::V0(m) = &out.message else {
            panic!("still v0");
        };

        // Resolve each instruction account the way the runtime does: static
        // keys first, then writable table entries, then readonly ones.
        let lookups = &m.address_table_lookups[0];
        let resolve = |i: usize| -> Pubkey {
            let statics = m.account_keys.len();
            if i < statics {
                return m.account_keys[i];
            }
            let w = lookups.writable_indexes.len();
            if i - statics < w {
                in_table[lookups.writable_indexes[i - statics] as usize]
            } else {
                in_table[lookups.readonly_indexes[i - statics - w] as usize]
            }
        };

        let original = &m.instructions[0];
        assert_eq!(resolve(original.program_id_index as usize), program);
        let touched: Vec<Pubkey> = original.accounts.iter().map(|a| resolve(*a as usize)).collect();
        assert_eq!(touched, vec![in_table[1], in_table[3]], "table accounts shifted");

        let memo_ix = m.instructions.last().unwrap();
        assert_eq!(resolve(memo_ix.program_id_index as usize), memo_program());
        assert_eq!(memo_ix.data, MEMO_TEXT);
    }

    /// The fee transfer must reach the fee wallet, and everything else must
    /// still reach what it reached before.
    ///
    /// The recipient is a WRITABLE account, which has to be inserted in the
    /// middle of the key list rather than appended — writable non-signers come
    /// before read-only ones. That renumbers every account after it, including
    /// the ones the swap resolves through its lookup table. If the shift is
    /// wrong the transaction still builds and still signs; it just moves the
    /// wrong money.
    #[test]
    fn a_fee_transfer_lands_without_renumbering_the_swap() {
        let payer = Keypair::new();
        let fee_wallet = Pubkey::new_unique();
        let in_table: Vec<Pubkey> = (0..4).map(|_| Pubkey::new_unique()).collect();
        let lut = AddressLookupTableAccount { key: Pubkey::new_unique(), addresses: in_table.clone() };
        let program = Pubkey::new_unique();
        let ix = Instruction::new_with_bytes(
            program,
            &[9, 9],
            vec![
                solana_sdk::instruction::AccountMeta::new(in_table[0], false),
                solana_sdk::instruction::AccountMeta::new_readonly(in_table[2], false),
            ],
        );
        let msg = v0::Message::try_compile(&payer.pubkey(), &[ix], &[lut], Hash::default())
            .expect("compiles");
        let tx = VersionedTransaction {
            signatures: vec![Signature::default()],
            message: VersionedMessage::V0(msg),
        };

        let out = decode(&attach_sol_transfer(&encode(&tx), &payer.pubkey(), &fee_wallet, 12_345));
        let VersionedMessage::V0(m) = &out.message else { panic!("still v0") };

        let lookups = &m.address_table_lookups[0];
        let resolve = |i: usize| -> Pubkey {
            let statics = m.account_keys.len();
            if i < statics { return m.account_keys[i]; }
            let w = lookups.writable_indexes.len();
            if i - statics < w { in_table[lookups.writable_indexes[i - statics] as usize] }
            else { in_table[lookups.readonly_indexes[i - statics - w] as usize] }
        };

        // The swap instruction is untouched in meaning.
        let swap = &m.instructions[0];
        assert_eq!(resolve(swap.program_id_index as usize), program);
        assert_eq!(
            swap.accounts.iter().map(|a| resolve(*a as usize)).collect::<Vec<_>>(),
            vec![in_table[0], in_table[2]],
            "the swap's accounts were renumbered",
        );

        // And the transfer says what it should.
        let transfer = m.instructions.last().unwrap();
        assert_eq!(resolve(transfer.program_id_index as usize), solana_sdk::system_program::id());
        assert_eq!(resolve(transfer.accounts[0] as usize), payer.pubkey());
        assert_eq!(resolve(transfer.accounts[1] as usize), fee_wallet);
        assert_eq!(u64::from_le_bytes(transfer.data[4..12].try_into().unwrap()), 12_345);

        // The recipient must be writable, or the transfer fails on chain.
        let fee_idx = m.account_keys.iter().position(|k| *k == fee_wallet).expect("present");
        let writable_end = m.account_keys.len() - m.header.num_readonly_unsigned_accounts as usize;
        assert!(fee_idx < writable_end, "fee wallet landed in the read-only region");
    }

    #[test]
    fn a_signed_transaction_is_left_alone() {
        // Magic Eden co-signs before we see it; changing the message would
        // silently invalidate their signature.
        let payer = Keypair::new();
        let msg = Message::new(
            &[system_instruction::transfer(&payer.pubkey(), &Pubkey::new_unique(), 5)],
            Some(&payer.pubkey()),
        );
        let tx: VersionedTransaction = Transaction::new(&[&payer], msg, Hash::default()).into();
        let encoded = encode(&tx);
        assert_eq!(attach(&encoded), encoded);
    }

    #[test]
    fn a_transaction_that_already_says_oprai_is_not_stamped_twice() {
        let payer = Pubkey::new_unique();
        let msg = Message::new(
            &[Instruction {
                program_id: memo_program(),
                accounts: vec![],
                data: MEMO_TEXT.to_vec(),
            }],
            Some(&payer),
        );
        let tx: VersionedTransaction = Transaction::new_unsigned(msg).into();
        let encoded = encode(&tx);
        assert_eq!(attach(&encoded), encoded);
    }

    #[test]
    fn garbage_in_is_garbage_out_not_a_panic() {
        assert_eq!(attach("not base64 at all!!"), "not base64 at all!!");
        assert_eq!(attach(""), "");
    }
}
