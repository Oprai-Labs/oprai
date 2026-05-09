# Jupiter DCA (Dollar Cost Averaging)

## Overview

Jupiter's DCA feature allows users to automatically split a large purchase into smaller orders executed at regular intervals. This is a time-tested investment strategy to reduce the impact of price volatility.

## How Jupiter DCA Works

1. User deposits input token (e.g., USDC) into a DCA account (a program-derived PDA)
2. User specifies: output token, total amount, number of orders, interval (minutes/hours/days)
3. Jupiter's keeper network triggers each order at the scheduled interval
4. Each order is executed as a Jupiter-routed swap across all available liquidity pools
5. Output tokens accumulate in the DCA account and can be withdrawn at any time

## Key Parameters

| Parameter | Description |
|-----------|-------------|
| Input token | Token to spend (USDC, SOL, etc.) |
| Output token | Token to accumulate |
| Total input | Total amount to deploy |
| # of orders | How many swaps to split into |
| Interval | Time between orders |
| Min/Max price | Optional price range bounds |

## Price Range Orders (Min/Max Price)

DCA supports conditional execution within a price range:
- If current price is outside the specified range, the keeper skips the order for that cycle
- Order resumes when price returns to range
- Useful for buying dips — set max price to only execute below a threshold

## DCA vs Regular Swap

| Feature | DCA | Swap |
|---------|-----|------|
| Price impact | Minimized (small orders) | Full impact on single trade |
| Timing | Automatic over time | Immediate |
| Slippage risk | Per-order | Full amount |
| Gas cost | Per-order (paid from order) | Single tx |
| Strategy | Time-based averaging | Spot execution |

## Technical Details

- DCA accounts are PDAs owned by the Jupiter DCA program
- Keeper network is decentralized — any keeper can trigger valid orders
- Orders use Jupiter's routing engine for best execution
- Output tokens stay in the PDA until withdrawn by user
- No withdrawal fee; gas per order is deducted from input balance

## Common Use Cases

- Accumulating SOL, JitoSOL, or mSOL over time from USDC
- Reducing single-trade price impact on illiquid tokens
- Systematic profit-taking (reverse DCA: sell output token over time)
- Setting a buy program that only triggers below a target price

## Closing a DCA

Users can close a DCA at any time:
- Remaining input tokens are returned to user's wallet
- Accumulated output tokens are transferred to user's wallet
- DCA PDA is closed and rent reclaimed
