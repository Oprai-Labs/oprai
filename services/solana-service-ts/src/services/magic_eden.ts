/**
 * Magic Eden — NFT marketplace REST API.
 * API key from X-NFT-API-KEY header if provided.
 */

import { appError, BuildResponse, ActionPreview } from "../types/index";
import { v4 as uuidv4 } from "uuid";

const ME_API = "https://api-mainnet.magiceden.dev/v2";
const ME_INSTRUCTIONS_API = "https://api-mainnet.magiceden.dev/v2";

function preview(type: string, description: string, params: Record<string, unknown>): ActionPreview {
  return {
    id: uuidv4(), type, description,
    estimatedFee: "~0.000005 SOL",
    params, warnings: [], requiresApproval: false,
  };
}

function meHeaders(): Record<string, string> {
  return { "Content-Type": "application/json" };
}

export interface MeBuyParams {
  mint: string;
  price: string;
  sellerAddress?: string;
  tokenSize?: number;
}

export async function buildMeBuy(params: MeBuyParams, userWallet: string): Promise<BuildResponse> {
  const url = new URL(`${ME_INSTRUCTIONS_API}/instructions/buy_now`);
  url.searchParams.set("buyer", userWallet);
  url.searchParams.set("seller", params.sellerAddress ?? "");
  url.searchParams.set("auctionHouseAddress", "E8cU1szgPNzDZFpLgLhYfUkH5jM4fjSPzLdcTxAHk9b3");
  url.searchParams.set("tokenMint", params.mint);
  url.searchParams.set("tokenATA", "");
  url.searchParams.set("price", params.price);
  url.searchParams.set("newPrice", params.price);

  const res = await fetch(url.toString(), { headers: meHeaders() });
  if (!res.ok) throw appError(`Magic Eden buy failed: ${await res.text()}`, 502, "ME_ERROR");
  const data = (await res.json()) as { tx?: { data: number[] }; transaction?: string };

  let txBase64: string;
  if (data.transaction) {
    txBase64 = data.transaction;
  } else if (data.tx?.data) {
    txBase64 = Buffer.from(data.tx.data).toString("base64");
  } else {
    throw appError("Magic Eden returned no transaction", 502, "ME_ERROR");
  }

  return {
    preview: preview("me_buy", `Buy NFT ${params.mint.slice(0, 8)}... for ${params.price} SOL on Magic Eden`, params as unknown as Record<string, unknown>),
    transaction: txBase64, additionalSignersRequired: 0, isCrossChain: false,
  };
}

export interface MeListParams {
  mint: string;
  price: string;
  tokenAccount?: string;
}

export async function buildMeList(params: MeListParams, userWallet: string): Promise<BuildResponse> {
  const url = new URL(`${ME_INSTRUCTIONS_API}/instructions/sell`);
  url.searchParams.set("seller", userWallet);
  url.searchParams.set("auctionHouseAddress", "E8cU1szgPNzDZFpLgLhYfUkH5jM4fjSPzLdcTxAHk9b3");
  url.searchParams.set("tokenMint", params.mint);
  url.searchParams.set("tokenAccount", params.tokenAccount ?? "");
  url.searchParams.set("price", params.price);
  url.searchParams.set("printReceipt", "true");

  const res = await fetch(url.toString(), { headers: meHeaders() });
  if (!res.ok) throw appError(`Magic Eden list failed: ${await res.text()}`, 502, "ME_ERROR");
  const data = (await res.json()) as { tx?: { data: number[] } };
  if (!data.tx?.data) throw appError("Magic Eden returned no transaction", 502, "ME_ERROR");

  return {
    preview: preview("me_list", `List NFT ${params.mint.slice(0, 8)}... for ${params.price} SOL on Magic Eden`, params as unknown as Record<string, unknown>),
    transaction: Buffer.from(data.tx.data).toString("base64"),
    additionalSignersRequired: 0, isCrossChain: false,
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Data queries
// ─────────────────────────────────────────────────────────────────────────────

export async function getMeCollectionInfo(params: { symbol: string }): Promise<BuildResponse> {
  const res = await fetch(`${ME_API}/collections/${params.symbol}`, { headers: meHeaders() });
  const data = res.ok ? await res.json() : {};
  return {
    preview: preview("me_collection_info", `Magic Eden collection: ${params.symbol}`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getMeNftInfo(params: { mint: string }): Promise<BuildResponse> {
  const res = await fetch(`${ME_API}/tokens/${params.mint}`, { headers: meHeaders() });
  const data = res.ok ? await res.json() : {};
  return {
    preview: preview("me_nft_info", `NFT info: ${params.mint}`, params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}

export async function getMeWalletNfts(params: { limit?: number; offset?: number }, userWallet: string): Promise<BuildResponse> {
  const url = `${ME_API}/wallets/${userWallet}/tokens?offset=${params.offset ?? 0}&limit=${params.limit ?? 100}`;
  const res = await fetch(url, { headers: meHeaders() });
  const data = res.ok ? await res.json() : [];
  return {
    preview: preview("me_wallet_nfts", "Wallet NFTs (Magic Eden)", params as unknown as Record<string, unknown>),
    additionalSignersRequired: 0, isCrossChain: false, data,
  };
}
