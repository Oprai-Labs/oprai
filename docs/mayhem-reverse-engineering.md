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

## The inner CPI cracked the layout

Mayhem's `Buy` CPIs into pump.fun's `buy`, and pump.fun's account order is
already known. Reading the inner instruction and mapping its accounts back to
the outer ones identifies almost everything at once — and turns up the fact
that made the earlier attempt fail:

**pump.fun's "user" is the Mayhem SOL vault, not the human.** The inner buy is
executed by `SOL_VAULT` into `mayhem_token_vault`; Mayhem then credits the
human in its own `mayhem_state`. That is why no PDA derived from the signer
matched anything: from pump.fun's side the signer is not the buyer.

Confirmed by derivation:
`user_volume_accumulator(SOL_VAULT)` = `FGFrX2q1iAjyAojjeyFDxXqdmvegjPpSWsrPmrJjeQ2f`,
which is account [16] in every sampled transaction regardless of mint or
signer.

```
outer  inner  what
[ 0]    —     the human: signer and fee payer
[ 1]    —     GLOBAL_PARAMS_ACCOUNT (constant)
[ 2]    —     mayhem_state          PDA(["mayhem-state", mint], mayhem)
[ 3]   [2]    mint
[ 4]   [5]    mayhem_token_vault    ATA(SOL_VAULT, mint, token-2022)
[ 5]   [6]    SOL_VAULT             ← the buyer, as pump.fun sees it
[ 6]   [7]    system program
[ 7]   [8]    token-2022 program
[ 8]   [0]    global                PDA(["global"], pumpfun)
[ 9]   [3]    bonding_curve         PDA(["bonding-curve", mint], pumpfun)
[10]   [1]    fee_recipient         (read from the global account)
[11]   [4]    assoc_bonding_curve   ATA(bonding_curve, mint, token-2022)
[12]   [9]    creator_vault         PDA(["creator-vault", creator], pumpfun)
[13]  [10]    EVENT_AUTHORITY (constant)
[14]  [11]    pump.fun program
[15]  [12]    PUMP_GLOBAL_VOLUME_ACC (constant)
[16]  [13]    user_volume_accumulator PDA([…, SOL_VAULT], pumpfun)
[17]  [14]    PUMP_FEE_CONFIG (constant)
[18]  [15]    PUMP_FEE_PROGRAM_ID (constant)
[19]    —     constant across every mint and signer sampled; seed not found,
              and not needed — it can be held as a constant the way the other
              observed pump.fun accounts already are
[20]    —     mayhem program
[21]  [16]    bonding_curve_v2      (our own buy passes this as remaining[0])
[22]  [17]    buyback fee config    (our own buy passes this as remaining[1])
```

Every account is therefore either a constant we hold, a PDA we already derive,
or one of the two remaining accounts our own bonding-curve buy already passes.

## Open questions

Only the arguments remain. The accounts are solved.

**The 20 argument bytes.** Read as `(u64, u64, u32)` the second field is zero
in every sample, which no client would send as a slippage bound. Comparing the
outer arguments against the inner pump.fun ones shows no fixed relationship
either:

```
outer a1 = 66,503,579,262   inner amount = 61,037,896,096,913  max_sol = 4,458,606,199
outer a1 = 18,094,505,834   inner amount =  1,321,745,667,054  max_sol =    23,945,931
outer a1 =  8,868,136,201   inner amount =    686,934,491,409  max_sol =     6,095,737
```

The ratios are 918, 73 and 77 — so `a1` is neither the token amount nor the
SOL cost. It is probably a position size in Mayhem's own accounting, which
would have to be read from `mayhem_state` to reproduce. The trailing bytes
begin `0x1388` = 5000 in every sample, with two varying bytes after it.

## How to finish

Read `mayhem_state` for a mint and see whether `a1` is expressible from it.
Failing that, disassemble the program for its argument struct — slower but
conclusive.

Until then a Mayhem buy cannot be built safely. Guessing an argument that
controls how much SOL leaves a wallet is not a thing to guess.
