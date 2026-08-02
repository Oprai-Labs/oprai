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
