# Liquid Staking on Solana

## What is Liquid Staking?

Liquid staking is a mechanism that allows SOL holders to earn staking rewards without locking their tokens. Instead of delegating SOL directly to a validator and waiting for the unstaking cooldown (typically 2-3 days), users deposit SOL into a liquid staking protocol and receive a liquid staking token (LST) in return.

The LST represents ownership in the staking pool and automatically appreciates in value relative to SOL as staking rewards accrue. Users can use LSTs across DeFi — as collateral, in liquidity pools, or in lending protocols — while still earning the base staking yield.

## Key Concepts

### Exchange Rate
LSTs appreciate against SOL rather than rebasing. The exchange rate (e.g., mSOL/SOL) increases over time as the pool accumulates staking rewards. This makes LSTs compatible with DeFi protocols that don't support rebasing tokens.

### Validator Diversification
Liquid staking protocols typically spread stake across many validators, reducing slashing risk from any single validator's downgrade. The protocol selects validators based on performance, commission rate, and decentralization metrics.

### Unstaking Cooldown
Converting LSTs back to native SOL requires going through the standard Solana unstaking epoch boundary (1-2 epochs ≈ 2-4 days). Most protocols offer instant unstake at a small fee by tapping liquidity reserves.

## Major Protocols

### Marinade Finance (mSOL)
Marinade is the first and largest Solana liquid staking protocol. It spreads stake across 400+ validators using an algorithmic selection mechanism that scores validators on performance, commission, and decentralization contribution. Users receive mSOL, which can be used across 20+ DeFi integrations.

- Native stake accounts are delegated via the Marinade smart contract
- mSOL:SOL exchange rate increases each epoch as rewards are harvested
- Instant unstake available at ~0.3% fee via the liquidity pool
- Governance: MNDE token, DAO-controlled parameters

### Jito (jitoSOL)
Jito is a liquid staking protocol built on top of Jito's MEV infrastructure. Validators running the Jito-Solana client share MEV tips with the staking pool, providing an additional yield source on top of base staking rewards.

- MEV tips create ~0.1-0.5% additional APY over vanilla staking
- Stake is distributed to Jito-client validators only
- jitoSOL accrues both base rewards and MEV tips in its exchange rate

### JupSOL
JupSOL is Jupiter's liquid staking token. Jupiter stakes into its own validator pool and passes all fees back to stakers. JupSOL is tightly integrated with Jupiter's swap and DCA infrastructure.

## APY Components

Solana staking APY consists of:
1. **Inflation rewards**: ~6-7% annualized from SOL token inflation, split among stakers proportionally
2. **Transaction fees**: Validators receive 50% of transaction fees from blocks they produce (the other 50% is burned)
3. **MEV/Priority fees**: For validators running MEV-aware clients like Jito, MEV tips add additional yield

## Risk Factors

- **Smart contract risk**: Funds in the staking contract are exposed to bugs
- **Validator slashing**: If validators are penalized, pool TVL decreases
- **Depegging**: LSTs can temporarily trade below par in secondary markets during periods of forced selling
- **Liquidity risk**: Instant unstake reserves may be depleted during high demand

## DeFi Integration

LSTs can be used as:
- Collateral in lending protocols (Kamino, Solend)
- Liquidity in DEX pools (Orca, Meteora) — earning swap fees on top of staking yield
- Basis for leveraged staking (Kamino Multiply) — recursively borrow and stake

The combination of staking yield + DeFi yield is referred to as "yield stacking."
