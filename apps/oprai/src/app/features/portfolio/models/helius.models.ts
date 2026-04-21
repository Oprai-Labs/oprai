export interface HeliusAssetContent {
  json_uri: string;
  metadata: {
    name: string;
    symbol: string;
    description?: string;
  };
  files?: Array<{ uri: string; mime?: string }>;
  links?: {
    image?: string;
    external_url?: string;
  };
}

export interface HeliusGrouping {
  group_key: string;
  group_value: string;
  collection_metadata?: {
    name?: string;
    symbol?: string;
    image?: string;
  };
  floor_price?: number;
}

export interface HeliusAsset {
  id: string;
  content: HeliusAssetContent;
  grouping: HeliusGrouping[];
  compression?: {
    compressed: boolean;
  };
  ownership: {
    owner: string;
  };
  token_info?: {
    supply?: number;
    decimals?: number;
  };
}

export interface HeliusAssetResponse {
  total: number;
  limit: number;
  page: number;
  items: HeliusAsset[];
}

export interface HeliusParsedTransaction {
  signature: string;
  timestamp: number;
  type: string;
  source: string;
  description: string;
  fee: number;
  feePayer: string;
  nativeTransfers?: Array<{
    fromUserAccount: string;
    toUserAccount: string;
    amount: number;
  }>;
  tokenTransfers?: Array<{
    fromUserAccount: string;
    toUserAccount: string;
    fromTokenAccount: string;
    toTokenAccount: string;
    tokenAmount: number;
    mint: string;
    tokenStandard: string;
  }>;
  events?: {
    swap?: {
      nativeInput?: { account: string; amount: string };
      nativeOutput?: { account: string; amount: string };
      tokenInputs?: Array<{ userAccount: string; tokenAccount: string; mint: string; rawTokenAmount: { tokenAmount: string; decimals: number } }>;
      tokenOutputs?: Array<{ userAccount: string; tokenAccount: string; mint: string; rawTokenAmount: { tokenAmount: string; decimals: number } }>;
    };
    nft?: {
      type: string;
      buyer: string;
      seller: string;
      amount: number;
      nfts: Array<{ mint: string; tokenStandard: string }>;
    };
  };
  transactionError?: string | null;
}
