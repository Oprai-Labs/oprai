# OpraiOS — AI Agent Platform for Solana

Python framework for building, training, and deploying DeFi AI agents. Standalone package — runs independently from polyglot services.

## Quick Start

```bash
cd opraios
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
.venv/bin/pytest                    # Run tests
.venv/bin/python -m opraios.mcp.server  # Start MCP server
```

## Architecture

```
                                    ┌─────────────────────────────────────┐
                                    │           OPRAIOS FRAMEWORK          │
                                    │                                     │
                                    │  ┌─────────────────────────────┐    │
                                    │  │      AgentBuilder API       │    │
                                    │  │   (Fluent + REST + MCP)     │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    │  ┌──────────────┴──────────────┐    │
                                    │  │      Plugin System           │    │
                                    │  │  ┌─────┬─────┬─────┬─────┐  │    │
                                    │  │  │Jupi-│Orca │Kamino│Drift│  │    │
                                    │  │  │ter  │     │      │     │  │    │
                                    │  │  └─────┴─────┴─────┴─────┘  │    │
                                    │  └──────────────┬──────────────┘    │
                                    │                 │                   │
                                    └─────────────────┼───────────────────┘
                                                      │
         ┌────────────────────────────────────────────┼───────────────────┐
         │                                            │                   │
         ▼                                            ▼                   ▼
    ┌─────────────┐                           ┌─────────────┐       ┌─────────────┐
    │   Solana    │                           │   LLM API   │       │  Templates  │
    │    RPC      │                           │ (OpenAI,    │       │ (16 agents) │
    │             │                           │  Anthropic) │       │             │
    └─────────────┘                           └─────────────┘       └─────────────┘
```

---

## File Structure

```
opraios/
├── pyproject.toml                 # Package config + dependencies
├── README.md
│
├── core/                          # Core framework
│   ├── agent_builder.py           # Fluent API for agent creation
│   ├── agent_builder_api.py       # REST API for agent management
│   ├── plugin_system.py           # Actions, Providers, Evaluators
│   ├── character.py               # Agent personality system
│   ├── config.py                  # Pydantic settings
│   │
│   ├── simulation.py              # Transaction dry-run
│   ├── gas_tracker.py             # Priority fee estimation
│   ├── safety.py                  # Fund movement protection
│   ├── confirmation.py            # Multi-step confirmation
│   ├── rollback.py                # Transaction undo/rollback
│   │
│   ├── scheduler.py               # Cron-based job scheduler
│   ├── notifications.py           # Multi-channel alerts
│   ├── multi_wallet.py            # Multi-wallet management
│   ├── tx_history.py              # Transaction export
│   │
│   ├── advanced_alerts.py         # Whale/smart money tracking
│   ├── copy_trading.py            # Copy trading system
│   ├── liquidation.py             # Position monitoring
│   │
│   ├── visual_builder.py          # Node-based workflow editor
│   ├── visual_builder_ui.py       # Flask + ReactFlow web UI
│   ├── visual_builder_api.py      # REST API for visual builder
│   ├── visual_builder_ws.py       # WebSocket support
│   │
│   ├── prompt_agent_creator.py    # AI-powered agent creation
│   ├── workflow_validator.py      # Workflow & config validation
│   ├── solana_nodes.py            # 25+ Solana DeFi nodes
│   │
│   ├── backtest_engine.py         # Strategy backtesting
│   ├── strategy_engine.py         # Strategy execution
│   ├── risk_manager.py            # Risk assessment
│   │
│   ├── llm.py                     # Multi-provider LLM client
│   ├── rag.py                     # RAG system
│   ├── database.py                # SQLAlchemy async
│   │
│   └── solvers/                   # LLM reasoning strategies
│       ├── react.py               # ReAct pattern
│       ├── chain_of_thought.py    # CoT reasoning
│       ├── tree_of_thought.py     # ToT exploration
│       ├── reflection.py          # Self-reflection
│       └── hybrid.py              # Combined approach
│
├── plugins/                       # DeFi protocol plugins
│   ├── __init__.py                # Plugin registry
│   ├── loader.py                  # Plugin loader
│   ├── manager.py                 # Plugin manager
│   ├── sandbox.py                 # Plugin sandboxing
│   │
│   ├── jupiter_plugin.py          # Jupiter DEX aggregator
│   ├── orca_plugin.py             # Orca CLMM
│   ├── raydium_plugin.py          # Raydium AMM
│   ├── meteora_plugin.py          # Meteora DLMM
│   ├── kamino_plugin.py           # Kamino lending
│   ├── drift_plugin.py            # Drift perps
│   ├── marginfi_plugin.py         # marginfi lending
│   ├── jito_plugin.py             # Jito staking + bundles
│   ├── marinade_plugin.py         # Marinade liquid staking
│   ├── blazestake_plugin.py       # BlazeStake LST
│   ├── solend_plugin.py           # Solend lending
│   ├── magic_eden_plugin.py       # Magic Eden NFT
│   ├── tensor_plugin.py           # Tensor NFT
│   │
│   ├── wormhole_plugin.py         # Wormhole bridge
│   ├── layerzero_plugin.py        # LayerZero bridge
│   ├── allbridge_plugin.py        # Allbridge
│   ├── squid_plugin.py            # Squid Router
│   ├── circle_cctp_plugin.py      # Circle CCTP
│   │
│   ├── dexscreener_plugin.py      # DexScreener data
│   ├── solscan_plugin.py          # Solscan analytics
│   ├── realms_plugin.py           # Realms DAO
│   ├── zero_one_plugin.py         # 0x1 protocol
│   │
│   └── example_plugin/            # Plugin template
│       ├── plugin.json
│       └── plugin.py
│
├── templates/                     # 16 pre-built agent templates
│   ├── manager.py                 # Template manager
│   │
│   ├── ai_trader.py               # AI-powered trading
│   ├── arbitrage.py               # Cross-DEX arbitrage
│   ├── liquidation.py             # Liquidation bot
│   ├── dca.py                     # Dollar cost averaging
│   ├── grid_trading.py            # Grid trading bot
│   ├── news_trading.py            # News-driven trading
│   ├── token_launch.py            # Token launch agent
│   │
│   ├── whale_follow.py            # Whale tracking
│   ├── airdrop_farmer.py          # Airdrop qualification
│   ├── staking_optimizer.py       # Liquid staking optimization
│   ├── portfolio_rebalancer.py    # Portfolio rebalancing
│   │
│   ├── trend_following.py         # Trend trading
│   ├── mean_reversion.py          # Mean reversion
│   ├── option_seller.py           # Covered calls
│   │
│   ├── bridge_agent.py            # Cross-chain transfers
│   └── security_auditor_agent.py  # Contract security
│
├── memory/                        # Long-term memory system
│   ├── vector_store.py            # Qdrant integration
│   ├── embedding_utils.py         # OpenAI embeddings
│   ├── retrieval.py               # Semantic search
│   ├── summarizer.py              # Text summarization
│   ├── compression.py             # Memory compression
│   ├── decay.py                   # Memory decay
│   └── graph_store.py             # Knowledge graph
│
├── scheduler/                     # Job scheduling
│   ├── __init__.py
│   └── runner.py                  # Strategy runner daemon
│
├── mcp/                           # Claude Code MCP integration
│   └── server.py                  # MCP server
│
├── context/                       # Context management
│   └── manager.py                 # Conversation context
│
├── tools/                         # Tool system
│   ├── hub.py                     # Tool hub
│   ├── registry.py                # Tool registry
│   └── connectors.py              # External connectors
│
├── security/                      # Security utilities
│   └── audit.py                   # Security audit
│
└── tests/                         # pytest test suite
    ├── test_simulation.py         # 33 tests
    ├── test_advanced_alerts.py    # 33 tests
    ├── test_rollback.py           # 13 tests
    ├── test_scheduler.py          # 17 tests
    └── ...
```

---

## Agent Builder

### Fluent API

```python
from opraios.core.agent_builder import AgentBuilder
from opraios.core.plugin_system import Action, Provider

# Create agent with fluent API
agent = (
    AgentBuilder()
    .name("DeFi Trader")
    .description("Autonomous DeFi trading agent")
    .model("gpt-4o")
    .temperature(0.7)
    .network("solana")
    .capability("swap", "Execute token swaps on Jupiter")
    .capability("stake", "Stake SOL on Marinade")
    .capability("lend", "Supply assets to Kamino")
    .memory(enabled=True, provider="qdrant")
    .voice(enabled=False)
    .social(twitter=False, discord=False)
    .webhook(url="https://...", events=["trade", "error"])
    .autonomous(enabled=True, interval_minutes=30)
    .build()
)

# Start agent
await agent.start()
```

### AgentConfig

```python
@dataclass
class AgentConfig:
    # Identity
    name: str
    description: str
    version: str = "1.0.0"

    # LLM Configuration
    model_provider: str = "openai"  # openai, anthropic, gemini, ollama, deepseek
    model_name: str = "gpt-4o"
    temperature: float = 0.7
    max_tokens: int = 4096

    # Blockchain
    networks: list[BlockchainNetwork] = field(default_factory=lambda: [BlockchainNetwork.SOLANA])
    wallet_address: Optional[str] = None

    # Features
    memory: AgentMemory
    voice: AgentVoice
    social: AgentSocial
    analytics: AgentAnalytics

    # Autonomous behavior
    autonomous_enabled: bool = False
    autonomous_interval_minutes: int = 30

    # Webhooks
    webhook_url: Optional[str] = None
    webhook_events: list[str] = field(default_factory=list)
```

---

## Plugin System

### Plugin Components

```python
from opraios.core.plugin_system import Action, Provider, Evaluator, ActionResult

# Action — executable with side effects
@dataclass
class Action:
    name: str
    description: str
    handler: Callable[..., Awaitable[ActionResult]]
    parameters: dict[str, Any]
    requires_auth: bool = False
    requires_wallet: bool = False
    category: str = "general"

# Provider — data fetcher (no side effects)
@dataclass
class Provider:
    name: str
    description: str
    handler: Callable[..., Awaitable[ProviderResult]]
    cache_ttl_seconds: int = 60
    category: str = "general"

# Evaluator — quality/safety checker
@dataclass
class Evaluator:
    name: str
    description: str
    handler: Callable[..., Awaitable[EvaluatorResult]]
    threshold: float = 0.7
    category: str = "quality"
```

### Example Plugin

```python
# plugins/example_plugin/plugin.py
from opraios.core.plugin_system import Action, Provider, ActionResult, ProviderResult

class ExamplePlugin:
    def get_actions(self) -> list[Action]:
        return [
            Action(
                name="example_swap",
                description="Execute a swap on Example DEX",
                handler=self._handle_swap,
                parameters={
                    "input_mint": {"type": "string", "required": True},
                    "output_mint": {"type": "string", "required": True},
                    "amount": {"type": "string", "required": True},
                },
                requires_wallet=True,
                category="trading",
            ),
        ]

    def get_providers(self) -> list[Provider]:
        return [
            Provider(
                name="example_price",
                description="Get token price from Example DEX",
                handler=self._get_price,
                cache_ttl_seconds=30,
            ),
        ]

    async def _handle_swap(self, params: dict, context: dict) -> ActionResult:
        # Implementation
        return ActionResult(success=True, data={"tx_hash": "..."})

    async def _get_price(self, params: dict) -> ProviderResult:
        # Implementation
        return ProviderResult(data={"price": 100.0})
```

### Plugin Manager

```python
from opraios.plugins.manager import PluginManager

manager = PluginManager()

# Install plugin
await manager.install("/path/to/plugin/")
await manager.install("https://github.com/user/plugin-repo")
await manager.install("https://github.com/user/repo/tree/develop")  # Specific branch

# List plugins
installed = manager.get_installed_plugins()
available = await manager.list_available()  # From registry

# Manage plugins
await manager.update("jupiter")
await manager.uninstall("old_plugin", remove_files=True, backup=True)
```

---

## Available Plugins

| Plugin | Category | Actions | Description |
|--------|----------|---------|-------------|
| `jupiter` | DEX | swap, limit_order, dca, lend, perp | DEX aggregator |
| `orca` | DEX | swap, add_liquidity, open_position | CLMM DEX |
| `raydium` | DEX | swap, add_liquidity, create_pool | AMM + CLMM |
| `meteora` | DEX | swap, add_liquidity, claim_fees | DLMM |
| `kamino` | Lending | deposit, borrow, multiply_open | Yield optimizer |
| `drift` | Perps | perp_open, deposit, twap | Perpetuals DEX |
| `marginfi` | Lending | deposit, borrow, repay | Lending protocol |
| `jito` | Staking | stake, unstake, bundle | MEV + staking |
| `marinade` | Staking | stake, unstake, delayed_unstake | Liquid staking |
| `blazestake` | Staking | stake, unstake | LST protocol |
| `solend` | Lending | deposit, borrow, liquidate | Lending |
| `magic_eden` | NFT | buy, list, make_offer | NFT marketplace |
| `tensor` | NFT | buy, list, make_offer | NFT marketplace |
| `wormhole` | Bridge | bridge, attest | Cross-chain |
| `layerzero` | Bridge | bridge | Cross-chain |
| `squid` | Bridge | cross_chain_swap | Squid Router |
| `dexscreener` | Data | get_pairs, get_token_info | DEX data |
| `solscan` | Data | get_transactions, get_token | Analytics |

---

## Agent Templates

### Trading Templates

| Template | File | Description |
|----------|------|-------------|
| AI Trading Agent | `ai_trader.py` | AI-powered market analysis & execution |
| Arbitrage Agent | `arbitrage.py` | Cross-DEX price difference exploitation |
| Liquidation Bot | `liquidation.py` | Lending protocol liquidation monitoring |
| DCA Agent | `dca.py` | Dollar cost averaging automation |
| Grid Trading Bot | `grid_trading.py` | Range-bound grid trading |
| News Trading Agent | `news_trading.py` | News-driven sentiment trading |
| Token Launch Agent | `token_launch.py` | New token launch detection |

### Yield & Portfolio

| Template | File | Description |
|----------|------|-------------|
| Whale Follow Agent | `whale_follow.py` | Smart money tracking |
| Airdrop Farmer | `airdrop_farmer.py` | Airdrop qualification automation |
| Staking Optimizer | `staking_optimizer.py` | Liquid staking optimization |
| Portfolio Rebalancer | `portfolio_rebalancer.py` | Target allocation maintenance |

### Technical Strategies

| Template | File | Description |
|----------|------|-------------|
| Trend Following | `trend_following.py` | Momentum-based trend trading |
| Mean Reversion | `mean_reversion.py` | Oscillator-based reversal trading |
| Option Seller | `option_seller.py` | Covered call/put selling |

### Utility

| Template | File | Description |
|----------|------|-------------|
| Bridge Agent | `bridge_agent.py` | Cross-chain transfer management |
| Security Auditor | `security_auditor_agent.py` | Smart contract security analysis |

---

## Simulation Mode

```python
from opraios.core.simulation import SimulationEngine, SimulationType

engine = SimulationEngine()

# Simulate a swap
result = await engine.simulate(
    SimulationType.SWAP,
    wallet_address="Hx7b8k...",
    params={
        "input_mint": "So11111111111111111111111111111111111111112",
        "output_mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "amount": "1000000000",  # 1 SOL
        "slippage_bps": 50,
    },
)

# Result structure
result.status         # SUCCESS, WARNING, FAILURE, ERROR
result.balance_changes  # List[BalanceChange]
result.fees           # List[FeeEstimate]
result.price_impact   # PriceImpactResult
result.route_preview  # RoutePreview
result.risk_assessment  # RiskAssessment
```

### Simulation Types

```python
class SimulationType(str, Enum):
    SWAP = "swap"
    TRANSFER = "transfer"
    STAKE = "stake"
    UNSTAKE = "unstake"
    LEND = "lend"
    WITHDRAW = "withdraw"
    BORROW = "borrow"
    REPAY = "repay"
    ADD_LIQUIDITY = "add_liquidity"
    REMOVE_LIQUIDITY = "remove_liquidity"
    BRIDGE = "bridge"
    LIMIT_ORDER = "limit_order"
    DCA = "dca"
    PERPETUAL = "perpetual"
```

### Price Impact Severity

```python
class PriceImpact(str, Enum):
    LOW = "low"          # < 1%
    MEDIUM = "medium"    # 1-3%
    HIGH = "high"        # 3-5%
    VERY_HIGH = "very_high"  # > 5%
    EXTREME = "extreme"   # > 10%
```

---

## Gas Tracker

```python
from opraios.core.gas_tracker import GasTracker, FeeStrategy, PriorityLevel

tracker = GasTracker()

# Get current priority fees
fees = await tracker.estimate_priority_fees()
# → {PriorityLevel.LOW: 1000, PriorityLevel.MEDIUM: 5000, PriorityLevel.HIGH: 10000, ...}

# With Jito tip floor
jito_fees = await tracker.estimate_with_jito_tip()

# Fee strategies
strategy = FeeStrategy.DYNAMIC  # FIXED, PERCENTILE, DYNAMIC, JITO
```

---

## Scheduler

```python
from opraios.core.scheduler import SchedulerService, ScheduleType

scheduler = SchedulerService()

# Create schedule
schedule = await scheduler.create_schedule(
    name="Daily SOL buy",
    action_type="swap",
    action_params={"inputMint": "USDC", "outputMint": "SOL", "amount": "100"},
    schedule_type=ScheduleType.CRON,
    cron_expression="0 9 * * *",  # Every day at 9 AM
)

# List schedules
schedules = await scheduler.list_schedules(wallet_address)

# Cancel
await scheduler.cancel_schedule(schedule_id)
```

---

## Notifications

```python
from opraios.core.notifications import NotificationService, Channel

notifier = NotificationService()

# Configure channels
notifier.add_channel(Channel.TELEGRAM, bot_token="...", chat_id="...")
notifier.add_channel(Channel.DISCORD, webhook_url="...")
notifier.add_channel(Channel.SLACK, webhook_url="...")

# Send notification
await notifier.send(
    title="Trade Executed",
    message="Swapped 1 SOL → 150 USDC",
    priority="high",
)
```

---

## Multi-Wallet Support

```python
from opraios.core.multi_wallet import MultiWalletManager

manager = MultiWalletManager()

# Link wallets
await manager.link_wallet(user_id, wallet_address, nickname="Trading wallet")
await manager.link_wallet(user_id, wallet_address_2, nickname="Holding wallet")

# Switch active wallet
manager.switch_wallet(user_id, wallet_address)

# Get aggregated balances
balances = await manager.get_aggregated_balances(user_id)

# Set permissions
manager.set_permissions(user_id, wallet_address, permissions={
    "view": True,
    "sign": True,
    "full": False,
})
```

---

## Visual Builder

### REST API

```python
from opraios.core.visual_builder_api import app

# Run Flask server
# GET /api/workflows - List workflows
# POST /api/workflows - Create workflow
# GET /api/workflows/{id} - Get workflow
# PUT /api/workflows/{id} - Update workflow
# DELETE /api/workflows/{id} - Delete workflow
# POST /api/workflows/{id}/execute - Execute workflow
# POST /api/workflows/{id}/validate - Validate workflow
```

### WebSocket

```python
from opraios.core.visual_builder_ws import WebSocketHandler

# Real-time workflow updates
# ws://localhost:5000/ws/workflows/{id}
```

### Solana Nodes

25+ node types for Solana DeFi:

| Category | Nodes |
|----------|-------|
| **Triggers** | On Wallet Activity, On Price Change, On Schedule, On Transaction |
| **DEX** | Jupiter Swap, Orca Swap, Raydium Swap, Meteora Swap |
| **Lending** | Kamino Deposit/Borrow, MarginFi Deposit, Solend Supply |
| **Staking** | Marinade Stake, Jito Stake, BlazeStake Stake |
| **Perps** | Drift Open Position, Jupiter Perp |
| **Bridge** | LayerZero Bridge, Squid Router, Wormhole |
| **NFT** | Magic Eden Buy/List, Tensor Buy/List |
| **Logic** | If/Else, Switch, Loop, Parallel, Delay |
| **Data** | Get Balance, Get Price, Get Portfolio, RPC Call |

---

## Testing

```bash
# Run all tests
.venv/bin/pytest tests/ -v

# Run specific test file
.venv/bin/pytest tests/test_simulation.py

# Run with coverage
.venv/bin/pytest tests/ --cov=opraios --cov-report=html

# Filter by name
.venv/bin/pytest -k "gas"

# Run with markers
.venv/bin/pytest -m "asyncio"
```

### Test Count

- `test_simulation.py` — 33 tests
- `test_advanced_alerts.py` — 33 tests
- `test_rollback.py` — 13 tests
- `test_scheduler.py` — 17 tests
- **Total: 313+ tests**

---

## Dependencies

```toml
[project]
dependencies = [
    "pydantic>=2.0.0",
    "aiohttp>=3.9.0",
    "websockets>=12.0.0",
    "openai>=1.0.0",
    "anthropic>=0.18.0",
    "qdrant-client>=1.7.0",
    "solana>=0.30.0",
    "solders>=0.18.0",
    "fastapi>=0.100.0",
    "uvicorn>=0.24.0",
    "sqlalchemy[asyncio]>=2.0.0",
    "asyncpg>=0.29.0",
    "croniter>=1.3.0",
    "flask>=3.0.0",
    "rich>=13.0.0",
    "click>=8.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0.0", "pytest-asyncio>=0.21.0", "ruff>=0.1.0", "mypy>=1.0.0"]
browser = ["playwright>=1.40.0"]
transcription = ["openai-whisper>=20231117"]
voice = ["pygame>=2.5.0"]
mcp = ["mcp>=0.9.0"]
```

---

## LLM Solvers

```python
from opraios.core.solvers import ReActSolver, ChainOfThoughtSolver, TreeOfThoughtSolver

# ReAct — Reasoning + Acting
solver = ReActSolver(llm_client)
result = await solver.solve("Swap 1 SOL to USDC and stake the output")

# Chain of Thought
solver = ChainOfThoughtSolver(llm_client)
result = await solver.solve("What's the best lending protocol for USDC?")

# Tree of Thought — explores multiple paths
solver = TreeOfThoughtSolver(llm_client, max_depth=3)
result = await solver.solve("Optimize my portfolio for yield")
```

---

## Memory System

```python
from opraios.memory.vector_store import VectorStore
from opraios.memory.retrieval import Retriever

store = VectorStore(collection_name="agent_memory")

# Store memory
await store.store(
    text="User prefers low-risk strategies",
    metadata={"user_id": "Hx7b8k...", "type": "preference"},
)

# Search
results = await store.search(
    query="What are my preferences?",
    top_k=5,
    filters={"user_id": "Hx7b8k..."},
)
```

---

## MCP Server

OpraiOS provides an MCP server for Claude Code integration:

```bash
# Start MCP server
.venv/bin/python -m opraios.mcp.server

# MCP tools exposed:
# - create_agent
# - execute_action
# - get_portfolio
# - simulate_transaction
# - list_schedules
```
