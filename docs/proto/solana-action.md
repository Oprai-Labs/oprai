# proto/solana/action.proto

Solana transaction building service. Transfer, swap, stake, token launch, and liquidity operations.

## File Information
- **Package**: `oprai.solana`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/solanapb`
- **Dependencies**: `google/protobuf/timestamp.proto`
- **Service**: `services/solana-service-rs/` (Rust)
- **Port**: **50053 (gRPC)** / **3030 (HTTP)**

---

## Service: SolanaActionService

Main Solana transaction service. Written in Rust.

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `BuildTransferTransaction` | BuildTransferRequest | BuildTransactionResponse | Build transfer TX |
| `BuildSwapTransaction` | BuildSwapRequest | BuildTransactionResponse | Build swap TX (Jupiter) |
| `BuildStakeTransaction` | BuildStakeRequest | BuildTransactionResponse | Build stake TX |
| `BuildLaunchTokenTransaction` | BuildLaunchTokenRequest | BuildTransactionResponse | Token launch TX (pump.fun) |
| `BuildLiquidityTransaction` | BuildLiquidityRequest | BuildTransactionResponse | Build LP TX |
| `SubmitTransaction` | SubmitTransactionRequest | SubmitTransactionResponse | Submit signed TX |
| `GetTransactionStatus` | GetTransactionStatusRequest | TransactionRecord | Query TX status |

---

## Action Flow

```
+----------+    +----------+    +----------------+    +----------+    +----------+
| Frontend |    | Gateway  |    | Solana Service |    |   RPC/DEX |    | Solana   |
| (Angular) |    |   (Go)   |    |    (Rust)    |    |  Protocols |    |  Network  |
+----+-----+    +----+-----+    +------+-------+    +----+-----+    +----+-----+
     |                |                        |
     | 1. Build TX    |                        |
     |-------------->|                        |
     |                | Build*Transaction()   |
     |                |----------------------->|
     |                |                        | Build unsigned TX
     |                |                        |
     |                |<-----------------------|
     |  { unsigned_tx, fee }                        |
     |<--------------|                        |
     |                |                        |
     | 2. Sign TX    |                        |
     |   (wallet)    |                        |
     |                |                        |
     | 3. Submit TX  |                        |
     |-------------->|                        |
     |                | SubmitTransaction()    |
     |                |----------------------->|
     |                |                        | Send to network
     |                |                        |
     |                |<-----------------------|
     |  { signature } |                        |
     |<--------------|                        |
     |                |                        |
     | 4. Poll status |                        |
     |-------------->|                        |
     |                | GetTransactionStatus() |
     |                |----------------------->|
     |                |<-----------------------|
     |  { confirmed } |                        |
     |<--------------|                        |
```

---

## Enums

### SolanaActionType

| Value | Number | Description |
|-------|--------|-------------|
| `SOLANA_ACTION_TYPE_UNSPECIFIED` | 0 | Unspecified |
| `SOLANA_ACTION_TYPE_TRANSFER` | 1 | Token/SOL transfer |
| `SOLANA_ACTION_TYPE_SWAP` | 2 | Jupiter swap |
| `SOLANA_ACTION_TYPE_LAUNCH_TOKEN` | 3 | pump.fun token launch |
| `SOLANA_ACTION_TYPE_STAKE` | 4 | Staking (native/Marinade/Jito) |
| `SOLANA_ACTION_TYPE_UNSTAKE` | 5 | Unstaking |
| `SOLANA_ACTION_TYPE_PROVIDE_LIQUIDITY` | 6 | Add LP |
| `SOLANA_ACTION_TYPE_REMOVE_LIQUIDITY` | 7 | Remove LP |
| `SOLANA_ACTION_TYPE_BRIDGE` | 8 | Cross-chain bridge |

---

### TransactionStatus

| Value | Number | Description |
|-------|--------|-------------|
| `TRANSACTION_STATUS_UNSPECIFIED` | 0 | Unspecified |
| `TRANSACTION_STATUS_PENDING` | 1 | Pending (unsigned) |
| `TRANSACTION_STATUS_SUBMITTED` | 2 | Submitted to network |
| `TRANSACTION_STATUS_CONFIRMED` | 3 | Confirmed (finalized) |
| `TRANSACTION_STATUS_FAILED` | 4 | Failed |
| `TRANSACTION_STATUS_CANCELLED` | 5 | Cancelled |

---

### StakeProtocol

| Value | Number | Description |
|-------|--------|-------------|
| `STAKE_PROTOCOL_UNSPECIFIED` | 0 | Unspecified |
| `STAKE_PROTOCOL_NATIVE` | 1 | Native staking (direct validator) |
| `STAKE_PROTOCOL_MARINADE` | 2 | Marinade Finance (mSOL) |
| `STAKE_PROTOCOL_JITO` | 3 | Jito Sol (jitoSOL) |

---

## Messages

### BuildTransactionResponse

All Build* methods return the same response.

| Field | Type | Description |
|-------|------|-------------|
| `transaction_id` | `string` | Internal tracking ID |
| `action_type` | `SolanaActionType` | Action type |
| `unsigned_transaction` | `string` | Base64-encoded unsigned TX |
| `estimated_fee` | `string` | Estimated fee (lamports) |
| `description` | `string` | Human-readable summary |
| `warnings` | `repeated string` | Warnings (high slippage, etc.) |
| `requires_approval` | `bool` | Is DeFi approval required? |

---

## Transfer Operations

### TransferParams

| Field | Type | Description |
|-------|------|-------------|
| `to` | `string` | Recipient wallet address |
| `amount` | `string` | Readable amount (e.g., "1.5") |
| `token` | `string` | "SOL" or mint address |
| `token_decimals` | `int32` | Token decimals (optional) |

### BuildTransferRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | Sender wallet |
| `params` | `TransferParams` | Transfer parameters |

**Example:**
```json
{
  "wallet": "Hx7b8kL9mN2pQ4rS6tU8vW0xY2zA4bC6d",
  "params": {
    "to": "9WzDX...abc",
    "amount": "10",
    "token": "SOL"
  }
}
```

**Backend:**
```rust
// services/solana-service-rs/src/actions/transfer.rs
pub async fn build_transfer(
    wallet: &Pubkey,
    params: TransferParams,
) -> Result<BuildTransactionResponse> {
    // 1. Get token mint
    let mint = if params.token == "SOL" {
        NATIVE_MINT
    } else {
        Pubkey::from_str(&params.token)?
        };

    // 2. Build instruction
    let instruction = if mint == NATIVE_MINT {
        system_instruction::transfer(&wallet, &params.to, params.amount_lamports)
    } else {
            // SPL token transfer via token program
            spl_token::instruction::transfer(...)
        };

    // 3. Create transaction
    let tx = Transaction::new_with_payer(&[instruction], &[wallet]);

    // 4. Get recent blockhash
    let blockhash = rpc.get_latest_blockhash().await?;

    // 5. Serialize
    Ok(BuildTransactionResponse {
        transaction_id: uuid(),
        unsigned_transaction: base64::encode(tx.serialize()),
        estimated_fee: tx.message().fee.calculate(),
        description: format!("Transfer {} {} to {}", params.amount, params.token, params.to),
    })
}
```

---

## Swap Operations (Jupiter)

### SwapParams

| Field | Type | Description |
|-------|------|-------------|
| `input_mint` | `string` | Source token mint |
| `output_mint` | `string` | Target token mint |
| `amount` | `string` | Input amount (raw units) |
| `slippage_bps` | `int32` | Slippage (basis points, 0 = default 50) |
| `only_direct_routes` | `bool` | Only direct routes |

### BuildSwapRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | Trader wallet |
| `params` | `SwapParams` | Swap parameters |

**Example:**
```json
{
  "wallet": "Hx7b8k...",
  "params": {
    "input_mint": "So11111111111111111111111111111111111111112",
    "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "amount": "1000000000",
    "slippage_bps": 50
  }
}
```

**Backend:**
```rust
// services/solana-service-rs/src/actions/swap.rs
pub async fn build_swap(
    wallet: &Pubkey,
    params: SwapParams,
) -> Result<BuildTransactionResponse> {
    // 1. Get quote from Jupiter
    let quote = jupiter_client.get_quote(
        &params.input_mint,
        &params.output_mint,
        &params.amount,
        params.slippage_bps,
    ).await?;

    // 2. Build swap instructions from route
    let instructions = jupiter_client.build_swap_instructions(&quote, wallet).await?;

    // 3. Create transaction
    let tx = Transaction::new_with_payer(&instructions, &[wallet]);

    // 4. Add warnings if needed
    let warnings = vec![];
    if quote.price_impact_pct > 1.0 {
        warnings.push(format!("High price impact: {:.2}%", quote.price_impact_pct));
    }

    Ok(BuildTransactionResponse {
        transaction_id: uuid(),
        unsigned_transaction: base64::encode(tx.serialize()),
        estimated_fee: estimate_fee(&tx),
        description: format!("Swap {} -> {}", params.input_mint, params.output_mint),
        warnings,
        requires_approval: false,
    })
}
```

---

## Stake Operations

### StakeParams

| Field | Type | Description |
|-------|------|-------------|
| `amount` | `string` | Stake amount (SOL) |
| `validator` | `string` | Validator pubkey (for native) |
| `protocol` | `StakeProtocol` | Stake protocol |
| `expected_jitosol` | `string` | Expected jitoSOL amount |
| `estimated_apy` | `string` | Estimated APY |
| `jitosol_amount` | `string` | jitoSOL to receive |
| `expected_sol` | `string` | Expected SOL (unstake) |
| `expected_msol` | `string` | Expected mSOL (Marinade) |
| `msol_amount` | `string` | mSOL to receive |
| `instant_unstake` | `bool` | Instant unstake (Marinade) |

### BuildStakeRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | Staker wallet |
| `params` | `StakeParams` | Stake parameters |

**Protocol-Specific Behavior:**

| Protocol | Action | Liquid Token |
|----------|--------|--------------|
| `NATIVE` | Direct stake to validator | - |
| `MARINADE` | Marinade via stake | mSOL |
| `JITO` | Jito stake pool | jitoSOL |

**Native Stake Example:**
```json
{
  "wallet": "Hx7b8k...",
  "params": {
    "amount": "10",
    "validator": "Gst4FD...",
    "protocol": "STAKE_PROTOCOL_NATIVE"
  }
}
```

**Marinade Stake Example:**
```json
{
  "wallet": "Hx7b8k...",
  "params": {
    "amount": "10",
    "protocol": "STAKE_PROTOCOL_MARINADE"
  }
}
```

---

## Token Launch (pump.fun)

### LaunchTokenParams

| Field | Type | Description |
|-------|------|-------------|
| `name` | `string` | Token name |
| `symbol` | `string` | Token symbol |
| `description` | `string` | Description |
| `image_url` | `string` | Logo URL |
| `twitter` | `string` | Twitter handle |
| `telegram` | `string` | Telegram link |
| `website` | `string` | Website URL |
| `initial_buy_amount` | `string` | Initial buy amount (SOL, optional) |

### BuildLaunchTokenRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | Creator wallet |
| `params` | `LaunchTokenParams` | Token parameters |

**Example:**
```json
{
  "wallet": "Hx7b8k...",
  "params": {
    "name": "My Awesome Token",
    "symbol": "MAT",
    "description": "The most awesome token",
    "image_url": "https://example.com/logo.png",
    "twitter": "@mytoken",
    "initial_buy_amount": "1"
  }
}
```

**Backend:**
```rust
// services/solana-service-rs/src/actions/launch_token.rs
pub async fn build_launch_token(
    wallet: &Pubkey,
    params: LaunchTokenParams,
) -> Result<BuildTransactionResponse> {
    // 1. Build pump.fun create instruction
    let instruction = pumpfun::create_token(
        &params.name,
        &params.symbol,
        &params.description,
        &params.image_url,
        &params.twitter,
        &params.telegram,
        &params.website,
    );

    // 2. Add initial buy if specified
    let mut instructions = vec![instruction];
    if let Some(buy_amount) = params.initial_buy_amount {
        let buy_ix = pumpfun::buy_instruction(wallet, &buy_amount);
        instructions.push(buy_ix);
    }

    // 3. Create transaction
    let tx = Transaction::new_with_payer(&instructions, &[wallet]);

    Ok(BuildTransactionResponse {
        transaction_id: uuid(),
        unsigned_transaction: base64::encode(tx.serialize()),
        description: format!("Launch token: {} ({})", params.name, params.symbol),
        requires_approval: true, // pump.fun requires approval
        ..Default::default()
    })
}
```

---

## Liquidity Operations

### LiquidityParams

| Field | Type | Description |
|-------|------|-------------|
| `pool_address` | `string` | Pool address |
| `token_a_mint` | `string` | Token A mint |
| `token_b_mint` | `string` | Token B mint |
| `amount_a` | `string` | Token A amount |
| `amount_b` | `string` | Token B amount |
| `slippage_bps` | `int32` | Slippage |
| `pool_id` | `string` | Pool ID (Orca/Raydium) |
| `lp_amount` | `string` | LP token amount (for remove) |

### BuildLiquidityRequest

| Field | Type | Description |
|-------|------|-------------|
| `wallet` | `string` | LP wallet |
| `params` | `LiquidityParams` | LP parameters |

---

## Submit Operation

### SubmitTransactionRequest

| Field | Type | Description |
|-------|------|-------------|
| `transaction_id` | `string` | ID from Build |
| `signed_transaction` | `string` | Base64-encoded signed TX |
| `wallet` | `string` | Sender wallet |

### SubmitTransactionResponse

| Field | Type | Description |
|-------|------|-------------|
| `signature` | `string` | On-chain transaction signature |
| `status` | `TransactionStatus` | Transaction status |

**Frontend Signing:**
```typescript
// apps/oprai/src/app/services/wallet.service.ts
async signAndSubmit(transactionId: string, unsignedTx: string): Promise<string> {
    // 1. Decode unsigned transaction
    const tx = VersionedTransaction.deserialize(bs58.decode(unsignedTx));

    // 2. Sign with wallet
    const signedTx = await this.wallet.signTransaction(tx);

    // 3. Submit to backend
    const { signature } = await this.http.post<SubmitResponse>('/solana/submit', {
        transaction_id: transactionId,
        signed_transaction: bs58.encode(signedTx.serialize()),
        wallet: this.wallet.publicKey
    }).toPromise();

    return signature;
}
```

---

## Transaction Record

### TransactionRecord

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Internal ID |
| `wallet` | `string` | Wallet |
| `session_id` | `string` | Associated chat session |
| `action` | `SolanaActionType` | Action type |
| `status` | `TransactionStatus` | Status |
| `signature` | `string` | On-chain signature |
| `error_message` | `string` | Error message |
| `created_at` | `Timestamp` | Creation |
| `updated_at` | `Timestamp` | Update |
| `confirmed_at` | `Timestamp` | Confirmation |

---

## Result Messages

Returns detailed results after transaction confirmation.

### TransferResult
| Field | Type |
|-------|------|
| `signature` | `string` |
| `from` | `string` |
| `to` | `string` |
| `amount` | `string` |
| `token` | `string` |

### SwapResult
| Field | Type |
|-------|------|
| `signature` | `string` |
| `input_mint` | `string` |
| `output_mint` | `string` |
| `in_amount` | `string` |
| `out_amount` | `string` |

### LaunchTokenResult
| Field | Type |
|-------|------|
| `signature` | `string` |
| `mint_address` | `string` |
| `name` | `string` |
| `symbol` | `string` |
| `bonding_curve_address` | `string` |

### StakeResult
| Field | Type |
|-------|------|
| `signature` | `string` |
| `amount` | `string` |
| `stake_account` | `string` |
| `liquid_staking_token` | `string` |

### LiquidityResult
| Field | Type |
|-------|------|
| `signature` | `string` |
| `pool_address` | `string` |
| `lp_token_amount` | `string` |

---

## API Endpoints (Gateway)

| HTTP | Endpoint | gRPC Call |
|------|----------|-----------|
| `POST` | `/solana/transfer/build` | `BuildTransferTransaction` |
| `POST` | `/solana/swap/build` | `BuildSwapTransaction` |
| `POST` | `/solana/stake/build` | `BuildStakeTransaction` |
| `POST` | `/solana/launch/build` | `BuildLaunchTokenTransaction` |
| `POST` | `/solana/liquidity/build` | `BuildLiquidityTransaction` |
| `POST` | `/solana/submit` | `SubmitTransaction` |
| `GET` | `/solana/tx/:id` | `GetTransactionStatus` |

---

## Database Schema

**Table:** `solana_schema.transactions`

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `wallet` | VARCHAR(66) | Wallet |
| `session_id` | UUID | Chat session |
| `action_type` | VARCHAR(30) | Action type |
| `status` | VARCHAR(20) | Status |
| `signature` | VARCHAR(88) | Transaction signature |
| `unsigned_tx` | TEXT | Serialized TX |
| `signed_tx` | TEXT | Signed TX |
| `estimated_fee` | BIGINT | Estimated fee |
| `error_message` | TEXT | Error |
| `created_at` | TIMESTAMP | Creation |
| `updated_at` | TIMESTAMP | Update |
| `confirmed_at` | TIMESTAMP | Confirmation |

---

## Integrated Protocols

| Protocol | Action Types | SDK |
|----------|-------------|-----|
| **Jupiter** | Swap | jupiter-quote-api |
| **Marinade** | Stake/Unstake | marinade-sdk |
| **Jito** | Stake/Unstake | jito-stake-sdk |
| **pump.fun** | Token Launch | pumpfun-sdk |
| **Orca** | Liquidity | orca-sdk |
| **Raydium** | Liquidity | raydium-sdk |
