/**
 * Action builder orchestrator.
 * Routes action types to the appropriate protocol service.
 */

import { appError, BuildResponse, BuildRequest } from "../types/index";

// Protocol services
import * as jupiter from "./jupiter";
import * as marginfi from "./marginfi";
import * as transfer from "./transfer";
import * as kamino from "./kamino";
import * as raydium from "./raydium";
import * as orca from "./orca";
import * as marinade from "./marinade";
import * as meteora from "./meteora";
import * as jito from "./jito";
import * as pumpfun from "./pumpfun";
import * as magicEden from "./magic_eden";
import * as dca from "./dca";
import * as limitOrder from "./limit_order";
import * as streamflow from "./streamflow";
import * as drift from "./drift";
type Params = Record<string, unknown>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function p<T>(params: Params): T { return params as any; }

export async function buildAction(
  req: BuildRequest,
  userWallet: string
): Promise<BuildResponse> {
  const { type, params } = req;

  switch (type) {
    // ── Transfer ─────────────────────────────────────────────────────────────
    case "transfer":
      return transfer.buildTransfer(p(params), userWallet);

    // ── Swap (Jupiter) ───────────────────────────────────────────────────────
    case "swap":
      return jupiter.buildSwap(p(params), userWallet);

    // ── DCA ──────────────────────────────────────────────────────────────────
    case "dca":
      return dca.buildDca(p(params), userWallet);
    case "cancel_dca":
      return dca.buildCancelDca(p(params), userWallet);
    case "jup_dca_orders":
      return dca.getDcaOrders(p(params), userWallet);

    // ── Limit orders ─────────────────────────────────────────────────────────
    case "limit_order":
      return limitOrder.buildLimitOrder(p(params), userWallet);
    case "cancel_limit_order":
      return limitOrder.buildCancelLimitOrder(p(params), userWallet);
    case "cancel_all_limit_orders":
      return limitOrder.buildCancelAllLimitOrders(params, userWallet);
    case "jup_limit_orders":
      return limitOrder.getLimitOrders(p(params), userWallet);

    // ── Jupiter queries ───────────────────────────────────────────────────────
    case "jup_price":
      return jupiter.getPrice(p(params));
    case "jup_token_search":
      return jupiter.searchTokens(p(params));

    // ── MarginFi ─────────────────────────────────────────────────────────────
    case "marginfi_create_account":
      return marginfi.buildCreateAccount(p(params), userWallet);
    case "marginfi_close_account":
      return marginfi.buildCloseAccount(p(params), userWallet);
    case "marginfi_deposit":
      return marginfi.buildDeposit(p(params), userWallet);
    case "marginfi_withdraw":
      return marginfi.buildWithdraw(p(params), userWallet);
    case "marginfi_borrow":
      return marginfi.buildBorrow(p(params), userWallet);
    case "marginfi_repay":
      return marginfi.buildRepay(p(params), userWallet);
    case "marginfi_liquidate":
      return marginfi.buildLiquidate(p(params), userWallet);
    case "marginfi_claim_emissions":
      return marginfi.buildClaimEmissions(p(params), userWallet);
    case "marginfi_loop":
      return marginfi.buildLoop(p(params), userWallet);
    case "marginfi_repay_with_collateral":
      return marginfi.buildRepayWithCollateral(p(params), userWallet);
    case "marginfi_flash_loan":
      return marginfi.buildFlashLoan(p(params), userWallet);
    case "marginfi_withdraw_all":
      return marginfi.buildWithdrawAll(p(params), userWallet);
    case "marginfi_move_position":
      return marginfi.buildMovePosition(p(params), userWallet);
    case "marginfi_account_info":
      return marginfi.getAccountInfo(p(params), userWallet);
    case "marginfi_banks":
      return marginfi.getBanks(params, userWallet);
    case "marginfi_user_accounts":
      return marginfi.getUserAccounts_(params, userWallet);
    case "marginfi_user_balances":
      // Detailed per-bank balance view (deposit/borrow side, USD, APY).
      // Used by the portfolio dashboard; transactions live in their own cases.
      return marginfi.getUserBalances(params, userWallet);
    case "marginfi_health":
      return marginfi.getHealth(p(params), userWallet);

    // ── Kamino ───────────────────────────────────────────────────────────────
    case "lend":
    case "kamino_deposit":
      return kamino.buildKaminoDeposit(p(params), userWallet);
    case "withdraw_lend":
    case "kamino_withdraw":
      return kamino.buildKaminoWithdraw(p(params), userWallet);
    case "borrow":
    case "kamino_borrow":
      return kamino.buildKaminoBorrow(p(params), userWallet);
    case "repay":
    case "kamino_repay":
      return kamino.buildKaminoRepay(p(params), userWallet);
    case "kamino_vault_deposit":
      return kamino.buildKaminoVaultDeposit(p(params), userWallet);
    case "kamino_vault_withdraw":
      return kamino.buildKaminoVaultWithdraw(p(params), userWallet);
    case "kamino_markets":
      return kamino.getKaminoMarkets(params);
    case "kamino_positions":
      return kamino.getKaminoUserPositions(p(params), userWallet);

    // ── Raydium ───────────────────────────────────────────────────────────────
    case "raydium_swap":
      return raydium.buildRaydiumSwap(p(params), userWallet);
    case "raydium_add_liquidity":
      return raydium.buildRaydiumAddLiquidity(p(params), userWallet);
    case "raydium_remove_liquidity":
      return raydium.buildRaydiumRemoveLiquidity(p(params), userWallet);
    case "raydium_get_pools":
    case "raydium_get_pools_v2":
      return raydium.getRaydiumPools(p(params));
    case "raydium_get_token_info":
      return raydium.getRaydiumTokenInfo(p(params));

    // ── Orca ─────────────────────────────────────────────────────────────────
    case "orca_swap":
      return orca.buildOrcaSwap(p(params), userWallet);
    case "orca_open_position":
      return orca.buildOrcaOpenPosition(p(params), userWallet);
    case "orca_close_position":
      return orca.buildOrcaClosePosition(p(params), userWallet);
    case "orca_collect_fees":
    case "orca_collect_rewards":
      return orca.buildOrcaCollectFees(p(params), userWallet);
    case "orca_get_pools":
    case "orca_search_pools":
      return orca.getOrcaPools(params);

    // ── Marinade ─────────────────────────────────────────────────────────────
    case "stake":
    case "marinade_stake":
      return marinade.buildMarinadeStake(p(params), userWallet);
    case "unstake":
    case "marinade_unstake":
      return marinade.buildMarinadeUnstake(p(params), userWallet);
    case "marinade_delayed_unstake":
      return marinade.buildMarinadeDelayedUnstake(p(params), userWallet);
    case "marinade_claim":
      return marinade.buildMarinadeClaim(p(params), userWallet);
    case "marinade_deposit_stake":
      return marinade.buildMarinadeDepositStake(p(params), userWallet);
    case "marinade_add_liquidity":
      return marinade.buildMarinadeAddLiquidity(p(params), userWallet);
    case "marinade_remove_liquidity":
      return marinade.buildMarinadeRemoveLiquidity(p(params), userWallet);
    case "marinade_state":
      return marinade.getMarinadeState(userWallet);
    case "marinade_exchange_rate":
      return marinade.getMarinadeExchangeRate(userWallet);
    case "marinade_ticket_info":
      return marinade.getMarinadeTicketInfo(p(params), userWallet);
    case "marinade_list_tickets":
      return marinade.listMarinadeTickets(userWallet);
    case "marinade_order_unstake_with_key":
      return marinade.buildMarinadeOrderUnstakeWithKey(p(params), userWallet);
    case "marinade_deposit_deactivating_stake":
      return marinade.buildMarinadeDepositDeactivatingStake(p(params), userWallet);
    case "marinade_partial_deposit_stake":
      return marinade.buildMarinadePartialDepositStake(p(params), userWallet);
    case "marinade_deposit_activating_stake":
      return marinade.buildMarinadeDepositActivatingStake(p(params), userWallet);
    case "marinade_all_tickets":
      return marinade.getAllMarinadeTickets(userWallet);
    case "marinade_ticket_due_date":
      return marinade.getMarinadeTicketDueDate(userWallet);
    case "marinade_referral_partner_state":
      return marinade.getMarinadeReferralPartnerState(p(params), userWallet);
    case "marinade_referral_global_state":
      return marinade.getMarinadeReferralGlobalState(userWallet);
    case "marinade_referral_partners":
      return marinade.getMarinadeReferralPartners(userWallet);
    case "marinade_deposit_stake_pool_token":
      return marinade.buildMarinadeDepositStakePoolToken(p(params), userWallet);
    case "marinade_liquidate_stake_pool_token":
      return marinade.buildMarinadeLiquidateStakePoolToken(p(params), userWallet);

    // ── Marinade Validators API ────────────────────────────────────────────────
    case "marinade_cluster_stats":
      return marinade.getMarinadeClusterStats(p(params));
    case "marinade_validator_scores":
      return marinade.getMarinadeValidatorScores();
    case "marinade_score_breakdown":
      return marinade.getMarinadeScoreBreakdown(p(params));
    case "marinade_score_breakdowns":
      return marinade.getMarinadeScoreBreakdowns(p(params));
    case "marinade_validator_commissions":
      return marinade.getMarinadeValidatorCommissions(p(params));
    case "marinade_validator_uptimes":
      return marinade.getMarinadeValidatorUptimes(p(params));
    case "marinade_validator_versions":
      return marinade.getMarinadeValidatorVersions(p(params));
    case "marinade_block_rewards":
      return marinade.getMarinadeBlockRewards();
    case "marinade_rewards":
      return marinade.getMarinadeRewards(p(params));
    case "marinade_unstake_hints":
      return marinade.getMarinadeUnstakeHints(p(params));
    case "marinade_global_unstake_hints":
      return marinade.getMarinadeGlobalUnstakeHints(p(params));
    case "marinade_jito":
      return marinade.getMarinadeJito();
    case "marinade_mev":
      return marinade.getMarinadeMev();
    case "marinade_staking_report":
      return marinade.getMarinadeStakingReport();
    case "marinade_scoring_report":
      return marinade.getMarinadeScoringReport();
    case "marinade_commission_changes":
      return marinade.getMarinadeCommissionChanges();

    // ── Marinade Historical API (api.marinade.finance) ────────────────────────
    case "marinade_msol_apy":
      return marinade.getMarinadeMsolApy(p(params));
    case "marinade_lp_apy":
      return marinade.getMarinadeLpApy(p(params));
    case "marinade_lp_price":
      return marinade.getMarinadeLpPrice(p(params));
    case "marinade_msol_supply":
      return marinade.getMarinadeMsolSupply(p(params));
    case "marinade_msol_price_sol":
      return marinade.getMarinadeMsolPriceSol(p(params));
    case "marinade_msol_price_usd":
      return marinade.getMarinadeMsolPriceUsd();
    case "marinade_farm_stats":
      return marinade.getMarinadeFarmStats(p(params));
    case "marinade_tlv_history":
      return marinade.getMarinadeTlvHistory(p(params));
    case "marinade_tlv":
      return marinade.getMarinadeTlv(p(params));

    // ── Marinade Snapshots API (snapshots-api.marinade.finance) ───────────────
    case "marinade_snapshot_msol":
      return marinade.getMarinadeSnapshotMsol(p(params));
    case "marinade_snapshot_vemnde":
      return marinade.getMarinadeSnapshotVemnde(p(params));
    case "marinade_stakers_all":
      return marinade.getMarinadeStakersAll(p(params));
    case "marinade_stakers_ns":
      return marinade.getMarinadeStakersNs(p(params));
    case "marinade_stakers_ns_all":
      return marinade.getMarinadeStakersNsAll();
    case "marinade_votes_msol_latest":
      return marinade.getMarinadeVotesMsolLatest();
    case "marinade_votes_msol_all":
      return marinade.getMarinadeVotesMsolAll(p(params));
    case "marinade_votes_vemnde_latest":
      return marinade.getMarinadeVotesVemndeLatest();
    case "marinade_votes_vemnde_all":
      return marinade.getMarinadeVotesVemndeAll(p(params));

    // ── Meteora ───────────────────────────────────────────────────────────────
    case "meteora_add_liquidity":
      return meteora.buildMeteoraAddLiquidity(p(params), userWallet);
    case "meteora_remove_liquidity":
      return meteora.buildMeteoraRemoveLiquidity(p(params), userWallet);
    case "meteora_claim_fees":
      return meteora.buildMeteoraClaimFees(p(params), userWallet);
    case "meteora_get_pools":
      return meteora.getMeteoraPools(p(params));
    case "meteora_user_positions":
      return meteora.getMeteoraUserPositions(p(params), userWallet);

    // ── Jito ─────────────────────────────────────────────────────────────────
    case "jito":
    case "jupsol_stake":
      return jito.buildJitoStake(p(params), userWallet);
    case "jito_unstake":
    case "jupsol_unstake":
      return jito.buildJitoUnstake(p(params), userWallet);

    // ── PumpFun ───────────────────────────────────────────────────────────────
    case "launch_token":
    case "pumpfun_launch":
      return pumpfun.buildLaunchToken(p(params), userWallet);
    case "pumpfun_buy":
      return pumpfun.buildPumpfunBuy(p(params), userWallet);
    case "pumpfun_sell":
      return pumpfun.buildPumpfunSell(p(params), userWallet);
    case "pumpfun_token_info":
      return pumpfun.getPumpfunTokenInfo(p(params));
    case "pumpfun_trending":
      return pumpfun.getPumpfunTrending(params);
    case "pumpfun_new":
      return pumpfun.getPumpfunNew(params);
    case "pumpfun_graduating":
      return pumpfun.getPumpfunGraduating(params);
    case "pumpfun_search":
      return pumpfun.searchPumpfunTokens(p(params));
    case "pumpfun_koth":
      return pumpfun.getPumpfunKingOfHill(params);
    case "pumpfun_comments":
      return pumpfun.getPumpfunTokenComments(p(params));
    case "pumpfun_user":
      return pumpfun.getPumpfunUserProfile(p(params));
    case "pumpfun_bonding_curve":
      return pumpfun.getPumpfunBondingCurve(p(params));

    // ── NFT / Magic Eden ──────────────────────────────────────────────────────
    case "nft_buy":
    case "me_buy":
      return magicEden.buildMeBuy(p(params), userWallet);
    case "nft_list":
    case "me_list":
      return magicEden.buildMeList(p(params), userWallet);
    case "me_collection_info":
      return magicEden.getMeCollectionInfo(p(params));
    case "me_nft_info":
      return magicEden.getMeNftInfo(p(params));
    case "me_wallet_nfts":
      return magicEden.getMeWalletNfts(p(params), userWallet);

    // ── Streamflow ────────────────────────────────────────────────────────────
    case "streamflow_create":
      return streamflow.buildStreamflowCreate(p(params), userWallet);
    case "streamflow_create_multiple":
      return streamflow.buildStreamflowCreateMultiple(p(params), userWallet);
    case "streamflow_cancel":
      return streamflow.buildStreamflowCancel(p(params), userWallet);
    case "streamflow_withdraw":
      return streamflow.buildStreamflowWithdraw(p(params), userWallet);
    case "streamflow_topup":
      return streamflow.buildStreamflowTopup(p(params), userWallet);
    case "streamflow_transfer":
      return streamflow.buildStreamflowTransfer(p(params), userWallet);
    case "streamflow_update":
      return streamflow.buildStreamflowUpdate(p(params), userWallet);
    case "streamflow_list":
      return streamflow.getStreamflowStreams(p(params), userWallet);
    case "streamflow_get_one":
      return streamflow.getStreamflowOne(p(params), userWallet);

    // ── Drift (perpetuals + spot) ────────────────────────────────────────────
    case "drift_list_positions":
      return drift.listDriftPositions(params, userWallet);

    default:
      throw appError(`Unknown action type: ${type}`, 400, "UNKNOWN_ACTION");
  }
}

export function validateAction(type: string, params: Params): void {
  // Basic presence check — detailed validation happens inside each builder
  if (!type || typeof type !== "string") {
    throw appError("action type is required", 400, "INVALID_PARAMS");
  }
  if (!params || typeof params !== "object") {
    throw appError("params must be an object", 400, "INVALID_PARAMS");
  }
}
