# pump.fun Mayhem — what has been decoded

Working notes for taking the Mayhem-routed buy in-house. Everything here was
read off mainnet transactions, not inferred from documentation — Mayhem has no
public IDL and its Anchor IDL account was not found at the usual PDA.

Why it matters: a token launched with `mayhem=true` does not trade through the
pump.fun bonding curve, so our own buy cannot serve it. That single gap is the
reason `pumpfun_initial_buy` still goes through PumpPortal, who take 0.5% of it.

Program: `MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e`

## Instruction catalogue

Collected by decoding 26 successful transactions and reading the
`Program log: Instruction: …` line against each discriminator.

| discriminator      | name           | data | accounts |
|--------------------|----------------|------|----------|
| `66063d1201daebea` | Buy            | 28 B | 23 |
| `b817ee6167c5d33d` | BuyV2          | 28 B | 32 |
| `de939d076c089151` | BuyPumpSwap    | 28 B | 31 |
| `33e685a4017f83ad` | Sell           | 28 B | 21 |
| `5df6823ce7e940b2` | SellV2         | 28 B | 31 |
| `10c691f0ee6589cb` | SellPumpSwap   | 28 B | 29 |

`Buy` carries the same discriminator as pump.fun's own `buy`. Mayhem mirrors
the interface and CPIs into pump.fun — its program sits at account [14] of its
own instruction and pump.fun's at [20].

## `Buy` — account layout

Decoded from `5iueoRyQmQkAZ5E7XHeYMa…`, mint
`BhYimNWfs6NVGceuiczi1qMHEmckNopX24gX4r9Kpump`. Identified by deriving every
PDA we already know how to build and matching addresses.

```
[ 0] signer / fee payer        NOT the buyer — see open questions
[ 1] GLOBAL_PARAMS_ACCOUNT     (constant we already hold)
[ 2] mayhem_state              PDA(["mayhem-state", mint], mayhem)
[ 3] mint
[ 4] mayhem_token_vault        ATA(SOL_VAULT, mint, token2022)
[ 5] SOL_VAULT                 (constant)
[ 6] system program
[ 7] token-2022 program
[ 8] global                    PDA(["global"], pumpfun)
[ 9] bonding_curve             PDA(["bonding-curve", mint], pumpfun)
[10] ?  74 bytes, owned by Mayhem            — a Mayhem PDA, purpose unknown
[11] assoc_bonding_curve       ATA(bonding_curve, mint, token2022)
[12] ?  system-owned, 0 bytes                — uninitialised; fee recipient or vault
[13] EVENT_AUTHORITY           (constant)
[14] pump.fun program
[15] PUMP_GLOBAL_VOLUME_ACC    (constant)
[16] ?  137 bytes, owned by pump.fun         — shape of a user_volume_accumulator
[17] PUMP_FEE_CONFIG           (constant)
[18] PUMP_FEE_PROGRAM_ID       (constant)
[19] ?  does not exist yet                   — created by the instruction
[20] mayhem program
[21] ?  does not exist yet                   — created by the instruction
[22] ?  208 bytes, owned by the pump fee program
```

Fifteen of twenty-three are already derivable with helpers in `pumpfun.rs`.

## `Buy` — argument layout

```
0..8    discriminator
8..16   u64   varies per trade — token amount
16..24  u64   always 0 across every sample
24..28  4 B   starts `8813` (0x1388 = 5000) with two varying bytes
```

The second u64 being zero in every observed buy is unexplained. If it is
`max_sol_cost`, a zero would mean no slippage bound, which no sane client
sends — so the layout is probably not (amount, max_sol_cost) and the reading
is wrong.

## Open questions

1. **Who is the buyer?** Account [0] signs and pays fees, but the SOL it loses
   is 0.000021 — fees only. None of the PDAs derived from it match [16], which
   has the shape of a per-user accumulator. The real buyer is elsewhere in the
   account list, and finding it is the key to the rest: several unknowns are
   user-derived PDAs that cannot be matched until the user is known.

2. **The trailing four bytes.** `0x1388` = 5000 is suspiciously round.

3. **[10], [12], [19], [21], [22]** — two are created by the instruction, so
   their seeds have to be inferred from what the program writes.

## How to finish

Correlate across many transactions: for each, take every account and derive
`user_volume_accumulator` and the ATAs from it; whichever reproduces [16] is
the buyer. With the buyer known, the remaining PDAs follow, and the builder is
mechanical after that.

Failing that, the Mayhem program's bytecode can be disassembled for its seed
strings — slower but conclusive.
