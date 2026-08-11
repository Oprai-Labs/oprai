import { Injectable, signal } from '@angular/core';

export interface ProtocolInfo {
  id: string;
  name: string;
  category: 'swap' | 'lend' | 'borrow' | 'perp' | 'stake' | 'nft' | 'bridge' | 'liquidity';
  icon: string;
  accent: string;
  accentBg: string;
  tvl?: number;
  apy?: number;
  actions: string[];
  description?: string;
}

export interface ProtocolStats {
  tvl: number;
  apy: number;
  volume24h?: number;
}

@Injectable({ providedIn: 'root' })
export class ProtocolRegistryService {
  // Protocol registry with metadata
  readonly protocols = signal<ProtocolInfo[]>([
    // NFT Marketplaces
    {
      id: 'tensor',
      name: 'Tensor',
      category: 'nft',
      icon: 'assets/icons/protocols/tensor.webp',
      accent: '#14B8A6',
      accentBg: 'rgba(20,184,166,0.08)',
      tvl: 150000000,
      actions: ['tensor_buy', 'tensor_list', 'tensor_make_offer', 'tensor_cancel_listing', 'tensor_cancel_offer'],
      description: '#1 NFT aggregator on Solana'
    },
    {
      id: 'magiceden',
      name: 'Magic Eden',
      category: 'nft',
      icon: 'assets/icons/protocols/magiceden.webp',
      accent: '#E42575',
      accentBg: 'rgba(228,37,117,0.08)',
      tvl: 80000000,
      actions: ['me_buy', 'me_list', 'me_make_offer', 'me_cancel_listing', 'me_accept_offer'],
      description: 'Leading NFT marketplace'
    },

    // Staking Protocols
    {
      id: 'jito',
      name: 'Jito',
      category: 'stake',
      icon: 'assets/icons/protocols/jito.webp',
      accent: '#3DBC73',
      accentBg: 'rgba(61,188,115,0.08)',
      tvl: 1200000000,
      apy: 7.2,
      actions: ['jito_stake', 'jito_unstake', 'jito_tip', 'jito_bundle'],
      description: 'Liquid staking with MEV tips'
    },
    {
      id: 'marinade',
      name: 'Marinade',
      category: 'stake',
      icon: 'assets/icons/protocols/marinade.webp',
      accent: '#FF7B00',
      accentBg: 'rgba(255,123,0,0.08)',
      tvl: 500000000,
      apy: 6.8,
      actions: ['marinade_stake', 'marinade_unstake', 'marinade_delayed_unstake'],
      description: 'Native liquid staking'
    },
    // Lending Protocols
    {
      id: 'kamino',
      name: 'Kamino',
      category: 'lend',
      icon: 'assets/icons/protocols/kamino.webp',
      accent: '#F4A918',
      accentBg: 'rgba(244,169,24,0.08)',
      tvl: 400000000,
      apy: 8.5,
      actions: ['kamino_deposit', 'kamino_withdraw', 'kamino_borrow', 'kamino_repay'],
      description: 'Isolated lending markets'
    },
    {
      id: 'solend',
      name: 'Solend',
      category: 'lend',
      icon: 'assets/icons/protocols/solend.svg',
      accent: '#6366F1',
      accentBg: 'rgba(99,102,241,0.08)',
      tvl: 150000000,
      apy: 7.8,
      actions: ['solend_deposit', 'solend_withdraw', 'solend_borrow', 'solend_repay'],
      description: 'Established lending protocol'
    },


    // DEX / Liquidity
    {
      id: 'jupiter',
      name: 'Jupiter',
      category: 'swap',
      icon: 'assets/icons/protocols/jupiter.webp',
      accent: '#C86EDD',
      accentBg: 'rgba(200,110,221,0.08)',
      tvl: 500000000,
      actions: ['swap', 'limit_order', 'dca', 'jupsol_stake', 'jupsol_unstake', 'lend', 'withdraw_lend', 'borrow', 'repay'],
      description: 'Best price routing'
    },
    {
      id: 'raydium',
      name: 'Raydium',
      category: 'liquidity',
      icon: 'assets/icons/protocols/raydium.webp',
      accent: '#5A31F4',
      accentBg: 'rgba(90,49,244,0.08)',
      tvl: 400000000,
      apy: 15.0,
      actions: ['raydium_swap', 'raydium_add_liquidity', 'raydium_remove_liquidity', 'raydium_open_position'],
      description: 'CLMM & Standard pools'
    },
    {
      id: 'orca',
      name: 'Orca',
      category: 'liquidity',
      icon: 'assets/icons/protocols/orca.webp',
      accent: '#00B4D8',
      accentBg: 'rgba(0,180,216,0.08)',
      tvl: 150000000,
      apy: 12.0,
      actions: ['orca_swap', 'orca_add_liquidity', 'orca_remove_liquidity', 'orca_open_position'],
      description: 'Whirlpool concentrated liquidity'
    },
    {
      id: 'meteora',
      name: 'Meteora',
      category: 'liquidity',
      icon: 'assets/icons/protocols/meteora.webp',
      accent: '#007AFF',
      accentBg: 'rgba(0,122,255,0.08)',
      tvl: 200000000,
      apy: 18.0,
      actions: ['meteora_swap', 'meteora_add_liquidity', 'meteora_remove_liquidity', 'meteora_open_position'],
      description: 'DLMM pools'
    },

    // Bridge
    {
      id: 'relay',
      name: 'Relay',
      category: 'bridge',
      icon: 'assets/icons/protocols/relay.webp',
      accent: '#8B5CF6',
      accentBg: 'rgba(139,92,246,0.08)',
      tvl: 100000000,
      actions: ['cross_chain_swap', 'bridge'],
      description: 'Cross-chain swaps'
    },
  ]);

  // Get protocols by category
  getProtocolsByCategory(category: ProtocolInfo['category']): ProtocolInfo[] {
    return this.protocols().filter(p => p.category === category);
  }

  // Get protocol by ID
  getProtocol(id: string): ProtocolInfo | undefined {
    return this.protocols().find(p => p.id === id);
  }

  // Get protocols that support a specific action
  getProtocolsForAction(actionType: string): ProtocolInfo[] {
    return this.protocols().filter(p => p.actions.includes(actionType));
  }

  // Get protocols sorted by TVL
  getTopProtocolsByTVL(category?: ProtocolInfo['category']): ProtocolInfo[] {
    let filtered = category ? this.getProtocolsByCategory(category) : this.protocols();
    return filtered.sort((a, b) => (b.tvl ?? 0) - (a.tvl ?? 0));
  }

  // Get protocols sorted by APY
  getTopProtocolsByAPY(category: ProtocolInfo['category']): ProtocolInfo[] {
    const filtered = this.getProtocolsByCategory(category);
    return filtered.sort((a, b) => (b.apy ?? 0) - (a.apy ?? 0));
  }

  // Get stake protocols sorted by APY
  getStakeProtocols(): ProtocolInfo[] {
    return this.getTopProtocolsByAPY('stake');
  }

  // Get NFT marketplace protocols
  getNftProtocols(): ProtocolInfo[] {
    return this.getProtocolsByCategory('nft');
  }

  // Get lending protocols sorted by APY
  getLendProtocols(): ProtocolInfo[] {
    return this.getTopProtocolsByAPY('lend');
  }

  // Get bridge protocols
  getBridgeProtocols(): ProtocolInfo[] {
    return this.getProtocolsByCategory('bridge');
  }

  // Get DEX protocols
  getDexProtocols(): ProtocolInfo[] {
    const categories: ProtocolInfo['category'][] = ['swap', 'liquidity'];
    return this.protocols()
      .filter(p => categories.includes(p.category))
      .sort((a, b) => (b.tvl ?? 0) - (a.tvl ?? 0));
  }

  // Generate clarify options for a category
  generateClarifyOptions(category: string): { label: string; sublabel?: string; value: string; icon: string; accent: string }[] {
    switch (category) {
      case 'stake':
        return this.getStakeProtocols().map(p => ({
          label: p.name,
          sublabel: p.apy ? `~${p.apy}% APY` : undefined,
          value: p.id,
          icon: p.icon,
          accent: p.accent,
        }));

      case 'nft':
        return this.getNftProtocols().map(p => ({
          label: p.name,
          sublabel: p.description,
          value: p.id,
          icon: p.icon,
          accent: p.accent,
        }));

      case 'lend':
        return this.getLendProtocols().map(p => ({
          label: p.name,
          sublabel: p.apy ? `~${p.apy}% APY` : undefined,
          value: p.id,
          icon: p.icon,
          accent: p.accent,
        }));

      case 'bridge':
        return this.getBridgeProtocols().map(p => ({
          label: p.name,
          sublabel: p.description,
          value: p.id,
          icon: p.icon,
          accent: p.accent,
        }));

      case 'swap':
      case 'liquidity':
        return this.getDexProtocols().slice(0, 5).map(p => ({
          label: p.name,
          sublabel: p.tvl ? `$${(p.tvl / 1e6).toFixed(0)}M TVL` : undefined,
          value: p.id,
          icon: p.icon,
          accent: p.accent,
        }));

      default:
        return [];
    }
  }
}
