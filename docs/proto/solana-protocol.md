# proto/solana/protocol.proto

Protocol and token metadata service. Supported DeFi protocols and token information.

## File Information
- **Package**: `oprai.solana`
- **Go Package**: `github.com/oprai/oprai/proto/gen/go/solanapb`
- **Service**: `services/solana-service-rs/` (Rust)

---

## Service: SolanaProtocolService

### RPC Methods

| Method | Request | Response | Description |
|--------|---------|----------|-------------|
| `ListProtocols` | ListProtocolsRequest | ListProtocolsResponse | List supported protocols |
| `GetTokenInfo` | GetTokenInfoRequest | TokenInfo | Get token info |
| `ListCommonTokens` | ListCommonTokensRequest | ListCommonTokensResponse | Common token list |

---

## Enum: DeFiProtocol

| Value | Number | Description | Action Types |
|-------|--------|-------------|--------------|
| `DEFI_PROTOCOL_UNSPECIFIED` | 0 | Unspecified | - |
| `DEFI_PROTOCOL_JUPITER` | 1 | Jupiter DEX Aggregator | Swap |
| `DEFI_PROTOCOL_PUMP_FUN` | 2 | pump.fun | Token Launch |
| `DEFI_PROTOCOL_RAYDIUM` | 3 | Raydium DEX | Swap, Liquidity |
| `DEFI_PROTOCOL_MARINADE` | 4 | Marinade Finance | Stake (mSOL) |
| `DEFI_PROTOCOL_JITO` | 5 | Jito | Stake (jitoSOL) |
| `DEFI_PROTOCOL_ORCA` | 6 | Orca DEX | Swap, Liquidity |
| `DEFI_PROTOCOL_METEORA` | 7 | Meteora | Liquidity |
| `DEFI_PROTOCOL_DRIFT` | 8 | Drift Protocol | Perps, Lend/Borrow |
| `DEFI_PROTOCOL_KAMINO` | 10 | Kamino | Lend/Borrow |

---

## Messages

### TokenInfo

| Field | Type | Description |
|-------|------|-------------|
| `address` | `string` | Mint address |
| `symbol` | `string` | Token symbol (e.g., "SOL", "USDC") |
| `name` | `string` | Full name (e.g., "Solana") |
| `decimals` | `int32` | Number of decimal places |
| `logo_uri` | `string` | Logo URL (optional) |

**Example:**
```json
{
  "address": "So11111111111111111111111111111111111111112",
  "symbol": "SOL",
  "name": "Wrapped SOL",
  "decimals": 9,
  "logo_uri": "https://raw.githubusercontent.com/solana-labs/token-list/main/assets/mainnet/So11111111111111111111111111111111111111112/logo.png"
}
```

---

### ProtocolInfo

| Field | Type | Description |
|-------|------|-------------|
| `protocol` | `DeFiProtocol` | Protocol enum |
| `name` | `string` | Display name |
| `description` | `string` | Description |
| `website` | `string` | Website URL |
| `supported_actions` | `repeated string` | Supported action types |

**Example:**
```json
{
  "protocol": "DEFI_PROTOCOL_JUPITER",
  "name": "Jupiter",
  "description": "Best DEX aggregator on Solana",
  "website": "https://jupiter.ag",
  "supported_actions": ["swap"]
}
```

---

### ListProtocolsRequest / ListProtocolsResponse

| Request | Empty - no parameters |

| Response Field | Type | Description |
|----------------|------|-------------|
| `protocols` | `repeated ProtocolInfo` | Protocol list |

---

### GetTokenInfoRequest

| oneof | Type | Description |
|-------|------|-------------|
| `address` | `string` | Query by mint address |
| `symbol` | `string` | Query by symbol |

**Note:** Only one should be sent.

---

### ListCommonTokensRequest / ListCommonTokensResponse

| Request Field | Type | Description |
|---------------|------|-------------|
| `network` | `string` | Network filter (default: "mainnet-beta") |

| Response Field | Type | Description |
|----------------|------|-------------|
| `tokens` | `repeated TokenInfo` | Token list |

---

## COMMON_TOKENS Registry

Application-defined common token list:

```rust
// services/solana-service-rs/src/tokens/common.rs
pub const COMMON_TOKENS: &[TokenInfo] = &[
    TokenInfo {
        address: "So11111111111111111111111111111111111111112",
        symbol: "SOL",
        name: "Wrapped SOL",
        decimals: 9,
        logo_uri: Some("..."),
    },
    TokenInfo {
        address: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        symbol: "USDC",
        name: "USD Coin",
        decimals: 6,
        logo_uri: Some("..."),
    },
    TokenInfo {
        address: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
        symbol: "USDT",
        name: "Tether USD",
        decimals: 6,
        logo_uri: Some("..."),
    },
    TokenInfo {
        address: "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",
        symbol: "mSOL",
        name: "Marinade SOL",
        decimals: 9,
        logo_uri: Some("..."),
    },
    TokenInfo {
        address: "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn",
        symbol: "jitoSOL",
        name: "Jito Staked SOL",
        decimals: 9,
        logo_uri: Some("..."),
    },
    // ... more tokens
];
```

---

## Protocol Features

### Jupiter (Swap Aggregator)
- **Action:** Swap
- **Features:**
  - Multi-DEX routing for best price
  - Slippage control
  - Price impact calculation
- **Website:** https://jupiter.ag

### Marinade (Liquid Staking)
- **Action:** Stake/Unstake
- **Features:**
  - mSOL liquid staking token
  - Automatic validator selection
  - Instant unstake (with fee)
- **Website:** https://marinade.finance

### Jito (Staking)
- **Action:** Stake/Unstake
- **Features:**
  - jitoSOL liquid staking token
  - MEV protection
  - High APY
- **Website:** https://jito.network

### pump.fun (Token Launch)
- **Action:** Token Launch
- **Features:**
  - Bonding curve mechanism
  - Low-cost token creation
  - Automatic liquidity
- **Website:** https://pump.fun

### Orca (DEX)
- **Action:** Swap, Liquidity
- **Features:**
  - Concentrated liquidity
  - Whirlpools
- **Website:** https://orca.so

### Raydium (DEX)
- **Action:** Swap, Liquidity
- **Features:**
  - AMM + Order book
  - Farming
- **Website:** https://raydium.io

### Drift (Perps)
- **Action:** Perpetuals, Lend/Borrow
- **Features:**
  - v2 AMM
  - Cross-margin
- **Website:** https://drift.trade

- **Action:** Lend/Borrow
- **Features:**
  - Cross-collateral
  - Risk parameters

### Kamino (Lending)
- **Action:** Lend/Borrow
- **Features:**
  - Automated strategies
  - kToken receipts
- **Website:** https://kamino.finance

---

## API Endpoints (Gateway)

| HTTP | Endpoint | gRPC Call |
|------|----------|-----------|
| `GET` | `/solana/protocols` | `ListProtocols` |
| `GET` | `/solana/tokens/:address` | `GetTokenInfo` |
| `GET` | `/solana/tokens` | `ListCommonTokens` |

---

## Usage Examples

### Frontend: Token Dropdown
```typescript
// apps/oprai/src/app/services/token.service.ts
@Injectable({ providedIn: 'root' })
export class TokenService {
    private http = inject(HttpClient);

    async getCommonTokens(): Promise<TokenInfo[]> {
        const response = await this.http.get<ListCommonTokensResponse>(
            '/solana/tokens'
        ).toPromise();
        return response.tokens;
    }

    async searchToken(query: string): Promise<TokenInfo[]> {
        // First try by symbol
        try {
            const token = await this.http.get<TokenInfo>(
                `/solana/tokens/${query}`
            ).toPromise();
            return [token];
        } catch {
            // If not found by symbol, search by address
            return this.searchByAddress(query);
        }
    }
}
```

### Chat Service: Token Context
```python
# services/chat-service-py/app/services/token_context.py
async def get_token_context(symbol: str) -> dict:
    """Get token info for LLM context."""
    token = await solana_stub.GetTokenInfo(GetTokenInfoRequest(symbol=symbol))

    return {
        "symbol": token.symbol,
        "name": token.name,
        "decimals": token.decimals,
        "mint": token.address,
    }
```

---

## Token Mint Reference

| Token | Symbol | Mint Address | Decimals |
|-------|--------|--------------|----------|
| SOL | SOL | `So11111111111111111111111111111111111111112` | 9 |
| USDC | USDC | `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` | 6 |
| USDT | USDT | `Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB` | 6 |
| mSOL | mSOL | `mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So` | 9 |
| jitoSOL | jitoSOL | `J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn` | 9 |
| BONK | BONK | `DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263` | 5 |
| RAY | RAY | `4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R` | 6 |
| ORCA | ORCA | `orcaEKTdK7LKz57vaAYr9QeNsVEPfiu6QeMU1kektZE` | 6 |
