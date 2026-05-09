# Protocol Plugins Reference

OpraiOS plugin sistemi — 20+ DeFi protocol entegrasyonu.

## Plugin Architecture

```python
from opraios.core.plugin_system import PluginBase, Action, Provider

class MyPlugin(PluginBase):
    @property
    def name(self) -> str: return "my_protocol"

    @property
    def version(self) -> str: return "1.0.0"

    async def initialize(self, config: dict | None = None):
        # Register actions
        self.register_action(Action(
            name="swap",
            description="Execute swap",
            handler=self._swap_handler,
            parameters={"inputMint": {"type": "string", "required": True}},
            requires_wallet=True,
            category="defi",
        ))

        # Register providers
        self.register_provider(Provider(
            name="price",
            description="Get token price",
            handler=self._price_handler,
            cache_ttl_seconds=30,
        ))
```

---

## DEX Plugins

### Jupiter (jupiter_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `swap` | inputMint, outputMint, amount, slippageBps | Execute token swap |
| `get_quote` | inputMint, outputMint, amount, slippageBps | Get swap quote |
| `limit_order` | inputMint, outputMint, amount, targetPrice, expiry | Create limit order |
| `cancel_limit_order` | orderKey | Cancel limit order |
| `dca` | inputMint, outputMint, amount, interval, numberOfOrders | Create DCA order |
| `cancel_dca` | dcaKey | Cancel DCA order |

**Providers:** `quote`, `price`, `limit_orders`, `dca_orders`

**API:** `https://quote-api.jup.ag/v6`, `https://api.jup.ag/trigger/v1`

---

### Orca (orca_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `orca_swap` | inputMint, outputMint, amount, slippageBps | Swap via Whirlpool |
| `orca_add_liquidity` | whirlpool, amountA, amountB | Add liquidity |
| `orca_remove_liquidity` | position, liquidityAmount | Remove liquidity |
| `orca_open_position` | whirlpool, amountA, amountB, tickLower, tickUpper | Open CL position |
| `orca_close_position` | position | Close position |
| `orca_increase_position` | position, amountA, amountB | Add to position |
| `orca_decrease_position` | position, liquidityAmount | Remove from position |
| `orca_collect_fees` | position | Collect trading fees |
| `orca_collect_rewards` | position | Collect reward tokens |

**Providers:** `whirlpools`, `whirlpool`, `positions`

**API:** `https://api.orca.so/v1/whirlpool`

---

### Raydium (raydium_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `raydium_swap` | inputMint, outputMint, amount, slippageBps | AMM/CLMM swap |
| `raydium_add_liquidity` | pool, amountA, amountB | Add to AMM pool |
| `raydium_remove_liquidity` | pool, lpAmount | Remove from AMM |
| `raydium_create_pool` | tokenA, tokenB, feeRate | Create new pool |
| `raydium_open_position` | pool, amountA, amountB, priceLower, priceUpper | Open CLMM position |
| `raydium_close_position` | position | Close position |
| `raydium_increase_position` | position, amountA, amountB | Add to position |
| `raydium_decrease_position` | position, liquidityAmount | Remove from position |

**Providers:** `pools`, `pool`, `positions`, `farms`

---

### Meteora (meteora_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `meteora_swap` | inputMint, outputMint, amount | DLMM swap |
| `meteora_add_liquidity` | pool, amount, bins | Add liquidity to bins |
| `meteora_remove_liquidity` | position, binIds, amounts | Remove from bins |
| `meteora_create_pool` | tokenA, tokenB, feeRate, binStep | Create DLMM pool |
| `meteora_open_position` | pool, amount, binRange | Open position |
| `meteora_close_position` | position | Close position |
| `meteora_add_to_position` | position, amount | Add to position |
| `meteora_claim_fees` | position | Claim trading fees |
| `meteora_claim_rewards` | position | Claim reward tokens |
| `meteora_stake` | amount, pool | Stake LP tokens |
| `meteora_unstake` | amount, pool | Unstake LP tokens |
| `meteora_harvest` | pool | Harvest staking rewards |

**Providers:** `pools`, `pool`, `positions`, `staking`

---

## Lending Plugins

### Kamino (kamino_plugin.py)

**K-Lend Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `kamino_deposit` | mint, amount | Deposit collateral |
| `kamino_withdraw` | mint, amount | Withdraw deposited |
| `kamino_borrow` | mint, amount | Borrow against collateral |
| `kamino_repay` | mint, amount | Repay borrowed |
| `kamino_add_collateral` | mint, amount | Add more collateral |
| `kamino_withdraw_collateral` | mint, amount | Withdraw collateral |

**Multiply Vaults:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `kamino_multiply_open` | vault, amount, leverage | Open leveraged yield position |
| `kamino_multiply_add` | position, amount | Add to multiply position |
| `kamino_multiply_withdraw` | position, amount | Withdraw from position |
| `kamino_multiply_close` | position | Close multiply position |

**Long/Short:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `kamino_long_open` | vault, amount, leverage | Open long position |
| `kamino_short_open` | vault, amount, leverage | Open short position |
| `kamino_position_close` | position | Close position |

**Liquidity Vaults:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `kamino_vault_deposit` | vault, amount | Deposit to CLMM vault |
| `kamino_vault_withdraw` | vault, shares | Withdraw from vault |

**KMNO Staking:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `kamino_stake` | amount | Stake KMNO |
| `kamino_unstake` | amount | Unstake KMNO |

**Providers:** `markets`, `reserves`, `obligations`, `multiply_vaults`, `long_short_vaults`, `liquidity_vaults`, `staking`

**API:** `https://api.kamino.finance`

---

### marginfi (marginfi_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `marginfi_create_account` | - | Create marginfi account |
| `marginfi_deposit` | mint, amount | Deposit to bank |
| `marginfi_withdraw` | mint, amount | Withdraw from bank |
| `marginfi_borrow` | mint, amount | Borrow from bank |
| `marginfi_repay` | mint, amount | Repay borrowed |
| `marginfi_deposit_collateral` | mint, amount | Deposit as collateral |
| `marginfi_withdraw_collateral` | mint, amount | Withdraw collateral |
| `marginfi_close_account` | - | Close account |

**Providers:** `banks`, `account_info`, `health`, `points`

---

### Solend (solend_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `solend_deposit` | mint, amount | Deposit to reserve |
| `solend_withdraw` | mint, amount | Withdraw deposited |
| `solend_borrow` | mint, amount | Borrow assets |
| `solend_repay` | mint, amount | Repay borrowed |
| `solend_add_collateral` | mint, amount | Add collateral |
| `solend_withdraw_collateral` | mint, amount | Withdraw collateral |
| `solend_liquidate` | obligation, collateralMint, debtMint, amount | Liquidate position |

**Providers:** `reserves`, `market`, `user_info`

---

## Perpetuals Plugins

### Drift (drift_plugin.py)

**Perpetuals:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `drift_perp_open` | market, direction, amount, leverage | Open perp position |
| `drift_perp_close` | market, amount | Close perp position |
| `drift_perp_add_collateral` | market, amount | Add collateral |
| `drift_perp_remove_collateral` | market, amount | Remove collateral |

**Spot:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `drift_spot_swap` | inputMint, outputMint, amount | Spot swap |
| `drift_deposit` | mint, amount | Deposit to account |
| `drift_withdraw` | mint, amount | Withdraw |
| `drift_borrow` | mint, amount | Borrow |
| `drift_repay` | mint, amount | Repay |

**Orders:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `drift_limit_order` | market, direction, amount, price | Place limit order |
| `drift_cancel_order` | orderId | Cancel order |
| `drift_twap` | market, direction, amount, interval | Create TWAP order |
| `drift_cancel_twap` | twapId | Cancel TWAP |

**Vaults:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `drift_vault_deposit` | vault, amount | Deposit to vault |
| `drift_vault_withdraw` | vault, shares | Withdraw from vault |

**Providers:** `markets`, `user_account`, `orders`, `orderbook`

**API:** `https://dlob.drift.trade`

---

## Staking Plugins

### Marinade (marinade_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `marinade_stake` | amount | Stake SOL → mSOL |
| `marinade_unstake` | amount | Unstake mSOL → SOL (instant) |
| `marinade_delayed_unstake` | amount | Delayed unstake (cheaper) |

**Providers:** `state`, `validators`, `stake_info`

**Program:** `MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYLDJgjq7aD`

---

### Jito (jito_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `jito_stake` | amount | Stake SOL → jitoSOL |
| `jito_unstake` | amount | Unstake jitoSOL |
| `jito_tip` | amount | Send tip to validators |
| `jito_bundle` | transactions[] | Submit bundle |
| `jito_bundle_status` | bundleId | Check bundle status |

**Providers:** `tip_floor`, `stake_info`, `bundle_status`

**API:** `https://mainnet.block-engine.jito.wtf`

---

### BlazeStake (blazestake_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `blazestake_stake` | amount | Stake SOL → bSOL |
| `blazestake_unstake` | amount | Unstake bSOL |

**Providers:** `stake_info`, `validators`

---

## NFT Plugins

### Magic Eden (magic_eden_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `me_buy` | mint, price | Buy NFT |
| `me_list` | mint, price | List NFT for sale |
| `me_cancel_listing` | mint | Cancel listing |
| `me_make_offer` | mint, price, expiry | Make offer |
| `me_accept_offer` | mint, offerId | Accept offer |
| `me_cancel_offer` | mint, offerId | Cancel offer |

**Providers:** `collection_info`, `nft_info`, `wallet_nfts`, `collection_activity`, `listings`, `offers`, `collection_nfts`

**API:** `https://api-mainnet.magiceden.dev`

---

### Tensor (tensor_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `tensor_buy` | mint, price | Buy NFT |
| `tensor_list` | mint, price | List NFT |
| `tensor_cancel_listing` | mint | Cancel listing |
| `tensor_make_offer` | mint, price, expiry | Make offer |
| `tensor_cancel_offer` | mint, offerId | Cancel offer |

**Providers:** `collection_info`, `nft_info`, `wallet_nfts`, `listings`

**API:** `https://api.tensor.so`

---

## Bridge Plugins

### Wormhole (wormhole_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `wormhole_bridge` | mint, amount, targetChain, targetAddress | Bridge tokens |
| `wormhole_attest` | mint | Attest token |

**Providers:** `chains`, `vaa_status`, `token_info`

---

### LayerZero (layerzero_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `layerzero_bridge` | mint, amount, targetChain, targetAddress | Bridge via LZ |

**Providers:** `chains`, `token_info`

---

### Squid Router (squid_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `squid_cross_chain_swap` | fromChain, toChain, fromToken, toToken, amount, slippage | Cross-chain swap |

**Providers:** `chains`, `tokens`, `route`

**API:** `https://api.0xsquid.com`

---

### Allbridge (allbridge_plugin.py)

**Actions:**
| Action | Parameters | Description |
|--------|------------|-------------|
| `allbridge_bridge` | sourceChain, targetChain, token, amount, recipient | Bridge tokens |

**Providers:** `chains`, `tokens`, `pools`

---

## Data Plugins

### DexScreener (dexscreener_plugin.py)

**Providers:**
| Provider | Parameters | Returns |
|----------|------------|---------|
| `pairs` | mint | List of trading pairs |
| `pair` | pairAddress | Pair details |
| `token_info` | mint | Token information |
| `boosts` | - | Boosted tokens |

**API:** `https://api.dexscreener.com`

---

### Solscan (solscan_plugin.py)

**Providers:**
| Provider | Parameters | Returns |
|----------|------------|---------|
| `transactions` | address, limit | Transaction list |
| `token` | mint | Token info |
| `token_holders` | mint, limit | Holder list |
| `token_markets` | mint | Market list |

**API:** `https://public-api.solscan.io`

---

## Plugin Manager

```python
from opraios.plugins.manager import PluginManager

manager = PluginManager()

# Install from various sources
await manager.install("/local/path/plugin/")           # Local directory
await manager.install("https://github.com/user/repo")   # GitHub
await manager.install("https://github.com/user/repo/tree/develop")  # Branch
await manager.install("https://example.com/plugin.zip") # Direct zip

# Manage plugins
installed = manager.get_installed_plugins()
await manager.update("jupiter")
await manager.uninstall("old_plugin", remove_files=True, backup=True)

# Get plugin info
info = await manager.get_plugin_info("jupiter")
```

---

## Creating a New Plugin

```python
# opraios/plugins/my_plugin/plugin.py

from opraios.core.plugin_system import PluginBase, Action, Provider

class MyPlugin(PluginBase):
    @property
    def name(self) -> str:
        return "my_protocol"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "My Protocol integration"

    async def initialize(self, config: dict | None = None):
        await super().initialize(config)

        # Register actions
        self.register_action(Action(
            name="swap",
            description="Execute swap on My Protocol",
            handler=self._swap_handler,
            parameters={
                "inputMint": {"type": "string", "required": True},
                "outputMint": {"type": "string", "required": True},
                "amount": {"type": "number", "required": True},
            },
            requires_wallet=True,
            category="defi",
        ))

    async def _swap_handler(self, params: dict, context: dict) -> ActionResult:
        # Implementation
        return ActionResult(success=True, data={"tx_hash": "..."})
```

---

## Plugin Registry

Built-in plugins available in the registry:

| Category | Plugins |
|----------|---------|
| DEX | jupiter, orca, raydium, meteora |
| Lending | kamino, marginfi, solend |
| Perps | drift |
| Staking | marinade, jito, blazestake |
| NFT | magic_eden, tensor |
| Bridge | wormhole, layerzero, squid, allbridge, circle_cctp |
| Data | dexscreener, solscan |
| DAO | realms |
