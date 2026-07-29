export interface Protocol {
  id: string;
  label: string;
  category: 'dex' | 'staking' | 'lending' | 'nft' | 'bridge' | 'streaming';
  logo: string;
  description: string;
}

export const PROTOCOLS: Protocol[] = [
  // DEX / Swap
  { id: 'jupiter',      label: 'Jupiter',      category: 'dex',       logo: 'assets/protocols/jupiter.png',      description: 'Best-rate token swaps' },
  { id: 'raydium',      label: 'Raydium',      category: 'dex',       logo: 'assets/protocols/raydium.png',      description: 'AMM liquidity pools' },
  { id: 'orca',         label: 'Orca',         category: 'dex',       logo: 'assets/protocols/orca.png',         description: 'Concentrated liquidity DEX' },
  // assets/protocols/meteora.png is a black-circle "M" that is not Meteora's
  // mark; point at the same file the cards use so the chip and the cards agree.
  { id: 'meteora',      label: 'Meteora',      category: 'dex',       logo: 'assets/icons/protocols/meteora.webp', description: 'DLMM + DAMM liquidity pools' },
  // Staking
  { id: 'marinade',     label: 'Marinade',     category: 'staking',   logo: 'assets/protocols/marinade.png',     description: 'mSOL liquid staking' },
  { id: 'jito',         label: 'Jito',         category: 'staking',   logo: 'assets/protocols/jito.png',         description: 'MEV-boosted staking (jitoSOL)' },
  // Lending
  { id: 'kamino',       label: 'Kamino',       category: 'lending',   logo: 'assets/protocols/kamino.png',       description: 'Lending & borrowing' },
  { id: 'marginfi',     label: 'MarginFi',     category: 'lending',   logo: 'assets/protocols/marginfi.png',     description: 'Margin lending protocol' },
  { id: 'solend',       label: 'Solend',       category: 'lending',   logo: 'assets/protocols/solend.png',       description: 'Algorithmic money market' },
  // NFT
  { id: 'tensor',       label: 'Tensor',       category: 'nft',       logo: 'assets/protocols/tensor.png',       description: 'NFT trading & sniping' },
  { id: 'magic_eden',   label: 'Magic Eden',   category: 'nft',       logo: 'assets/protocols/magic_eden.png',   description: 'NFT marketplace' },
  { id: 'pumpfun',      label: 'Pump.fun',     category: 'nft',       logo: 'assets/protocols/pumpfun.png',      description: 'Token launches' },
  // Bridge
  { id: 'relay',        label: 'Relay',        category: 'bridge',    logo: 'assets/protocols/relay.png',        description: 'Cross-chain bridging' },
  { id: 'debridge',     label: 'deBridge',     category: 'bridge',    logo: 'assets/protocols/debridge.png',     description: 'Cross-chain transfers' },
  { id: 'squid',        label: 'Squid',        category: 'bridge',    logo: 'assets/protocols/squid.png',        description: 'Cross-chain swaps' },
  // Streaming
  { id: 'streamflow',   label: 'Streamflow',   category: 'streaming', logo: 'assets/protocols/streamflow.png',   description: 'Token streaming & vesting' },
];

export const CATEGORY_COLORS: Record<Protocol['category'], string> = {
  dex:       '#06B6D4',
  staking:   '#8B5CF6',
  lending:   '#F59E0B',
  nft:       '#EC4899',
  bridge:    '#10B981',
  streaming: '#5b5fc7',
};
