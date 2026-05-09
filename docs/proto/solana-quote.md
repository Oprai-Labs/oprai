# proto/solana/quote.proto

Swap quote service. Real-time price quotes via the Jupiter aggregator.

## File Information
- **Package**: `oprai.solana`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/solanapb`
- **Service**: `services/solana-service-rs/` (Rust)

---

## Service: SolanaQuoteService

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `GetSwapQuote` | GetSwapQuoteRequest | SwapQuote | Get quote from Jupiter |

---

## Messages

### GetSwapQuoteRequest

| Field | Type | Description |
|-------|------|-------------|
| `input_mint` | `string` | Source token mint address |
| `output_mint` | `string` | Target token mint address |
| `amount` | `string` | Amount (raw units - lamports/token units) |
| `slippage_bps` | `int32` | Slippage tolerance (basis points, 0 = default 50) |
| `only_direct_routes` | `bool` | Only direct routes |

**Example:**
```json
{
  "input_mint": "So11111111111111111111111111111111111111112",
  "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "amount": "1000000000",
  "slippage_bps": 50,
  "only_direct_routes": false
}
```

---

### SwapQuote

| Field | Type | Description |
|-------|------|-------------|
| `input_mint` | `string` | Input token mint |
| `output_mint` | `string` | Output token mint |
| `in_amount` | `string` | Input amount (raw) |
| `out_amount` | `string` | Expected output (raw) |
| `other_amount_threshold` | `string` | Minimum acceptable output |
| `swap_mode` | `string` | "ExactIn" \| "ExactOut" |
| `slippage_bps` | `int32` | Slippage used |
| `price_impact_pct` | `string` | Price impact (%) |
| `route_plan` | `repeated RoutePlanStep` | Route steps |
| `platform_fee` | `PlatformFee` | Platform fee (optional) |

**Example Response:**
```json
{
  "input_mint": "So11111111111111111111111111111111111111112",
  "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "in_amount": "1000000000",
  "out_amount": "150420000",
  "other_amount_threshold": "149670000",
  "swap_mode": "ExactIn",
  "slippage_bps": 50,
  "price_impact_pct": "0.01",
  "route_plan": [
    {
      "swap_info": {
        "amm_key": "Raydium",
        "label": "Raydium",
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "in_amount": "1000000000",
        "out_amount": "150420000",
        "fee_amount": "1504",
        "fee_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
      },
      "percent": 100
    }
  ]
}
```

---

### RoutePlanStep

| Field | Type | Description |
|-------|------|-------------|
| `swap_info` | `SwapInfo` | Swap details |
| `percent` | `int32` | Percentage of total (0-100) |

**Multi-Route Example:**
```json
{
  "route_plan": [
    {
      "swap_info": { "amm_key": "Orca", ... },
      "percent": 60
    },
    {
      "swap_info": { "amm_key": "Raydium", ... },
      "percent": 40
    }
  ]
}
```
-> 60% of the transaction goes through Orca, 40% through Raydium.

---

### SwapInfo

| Field | Type | Description |
|-------|------|-------------|
| `amm_key` | `string` | AMM identifier |
| `label` | `string` | Display name |
| `input_mint` | `string` | Input token |
| `output_mint` | `string` | Output token |
| `in_amount` | `string` | Input amount |
| `out_amount` | `string` | Output amount |
| `fee_amount` | `string` | Fee amount |
| `fee_mint` | `string` | Fee token |

---

### PlatformFee

| Field | Type | Description |
|-------|------|-------------|
| `amount` | `string` | Fee amount |
| `fee_bps` | `int32` | Fee rate (basis points) |

---

## Jupiter API Integration

```rust
// services/solana-service-rs/src/quote/jupiter.rs
pub struct JupiterClient {
    base_url: String,
}

impl JupiterClient {
    pub async fn get_quote(
        &self,
        input_mint: &str,
        output_mint: &str,
        amount: &str,
        slippage_bps: i32,
    ) -> Result<SwapQuote> {
        let url = format!(
            "{}/quote?inputMint={}&outputMint={}&amount={}&slippageBps={}",
            self.base_url, input_mint, output_mint, amount, slippage_bps
        );

        let response = reqwest::get(&url).await?.json::<JupiterQuote>().await?;

        Ok(SwapQuote {
            input_mint: response.input_mint,
            output_mint: response.output_mint,
            in_amount: response.in_amount,
            out_amount: response.out_amount,
            other_amount_threshold: response.other_amount_threshold,
            swap_mode: response.swap_mode,
            slippage_bps: response.slippage_bps,
            price_impact_pct: response.price_impact_pct,
            route_plan: response.route_plan.into_iter().map(|step| {
                RoutePlanStep {
                    swap_info: SwapInfo {
                        amm_key: step.swap_info.amm_key,
                        label: step.swap_info.label,
                        // ...
                    },
                    percent: step.percent,
                }
            }).collect(),
            platform_fee: response.platform_fee.map(|f| PlatformFee {
                amount: f.amount,
                fee_bps: f.fee_bps,
            }),
        })
    }
}
```

---

## Usage Flow

```
1. User: "Convert 1 SOL to USDC"
        |
2. Chat Service -> SolanaQuoteService.GetSwapQuote
        |
3. Jupiter API -> Quote response
        |
4. AI Response:
   "1 SOL = 150.42 USDC
   Price impact: 0.01%
   Route: Raydium

   [ACTION:swap]
   input_mint=So11111111111111111111111111111111111111112
   output_mint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
   amount=1000000000
   slippage_bps=50"
        |
5. Frontend -> BuildSwapTransaction -> Sign -> Submit
```

---

## Price Impact Levels

| Price Impact | Risk Level | Recommendation |
|--------------|------------|----------------|
| < 0.5% | Low | Safe |
| 0.5% - 1% | Medium | Acceptable |
| 1% - 3% | High | Caution |
| > 3% | Very High | Show warning |

---

## API Endpoint

| HTTP | Endpoint | gRPC Call |
|------|----------|-----------|
| `GET` | `/solana/quote` | `GetSwapQuote` |

**Query Parameters:**
- `input_mint` - Source token
- `output_mint` - Target token
- `amount` - Amount
- `slippage_bps` - Slippage (optional)
- `only_direct_routes` - Direct route only (optional)
