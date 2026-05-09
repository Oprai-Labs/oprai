import { TestBed } from '@angular/core/testing';
import { IntentParserService, ParsedAction, ParsedQuery, ParsedClarify } from './intent-parser.service';

describe('IntentParserService', () => {
  let service: IntentParserService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(IntentParserService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  // =====================
  // ACTION PARSING TESTS
  // =====================

  describe('parse() - Action parsing', () => {
    it('should parse JSON format action blocks', () => {
      const content = 'Swap my SOL to USDC [ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", "amount": "1"}';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('swap');
      expect(result[0].params['inputMint']).toBe('SOL');
      expect(result[0].params['outputMint']).toBe('USDC');
      expect(result[0].params['amount']).toBe('1');
    });

    it('should parse legacy key=value format action blocks', () => {
      const content = 'Send 1 SOL to HwMdhvXYPPDircKQ7ub45XeMAeohJL4AT3V5CtshXtK6 [ACTION:transfer] to=HwMdhvXYPPDircKQ7ub45XeMAeohJL4AT3V5CtshXtK6 amount=1 token=SOL';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('transfer');
      expect(result[0].params['to']).toBe('HwMdhvXYPPDircKQ7ub45XeMAeohJL4AT3V5CtshXtK6');
      expect(result[0].params['amount']).toBe('1');
      expect(result[0].params['token']).toBe('SOL');
    });

    it('should filter unknown action types', () => {
      const content = '[ACTION:unknown_action] foo=bar';
      const result = service.parse(content);

      expect(result.length).toBe(0);
    });

    it('should filter snipe_token (not supported)', () => {
      const content = '[ACTION:snipe_token] token=BONK amount=1';
      const result = service.parse(content);

      expect(result.length).toBe(0);
    });

    it('should parse multiple actions in one message', () => {
      const content = `
        First swap [ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", "amount": "1"}
        Then stake [ACTION:stake] {"amount": "10", "protocol": "marinade"}
      `;
      const result = service.parse(content);

      expect(result.length).toBe(2);
      expect(result[0].type).toBe('swap');
      expect(result[1].type).toBe('stake');
    });

    it('should handle nft_buy with marketplace parameter', () => {
      const content = '[ACTION:nft_buy] {"mint": "7xKXtg...", "marketplace": "tensor"}';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('nft_buy');
      expect(result[0].params['marketplace']).toBe('tensor');
    });

    it('should handle nft_buy with magic_eden marketplace', () => {
      const content = '[ACTION:nft_buy] {"mint": "7xKX...", "marketplace": "magic_eden"}';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('nft_buy');
      expect(result[0].params['marketplace']).toBe('magic_eden');
    });

    // ---- Limit order action tests ----

    it('should parse limit_order JSON format with targetPrice', () => {
      const content = '[ACTION:limit_order] {"inputMint": "SOL", "outputMint": "USDC", "amount": "10", "targetPrice": "105"}';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('limit_order');
      expect(result[0].params['inputMint']).toBe('SOL');
      expect(result[0].params['outputMint']).toBe('USDC');
      expect(result[0].params['amount']).toBe('10');
      expect(result[0].params['targetPrice']).toBe('105');
    });

    it('should parse limit_order legacy key=value format with targetPrice', () => {
      const content = '[ACTION:limit_order] inputMint=SOL outputMint=USDC amount=10 targetPrice=105';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('limit_order');
      expect(result[0].params['inputMint']).toBe('SOL');
      expect(result[0].params['targetPrice']).toBe('105');
    });

    it('should parse limit_order legacy key=value format with old "price" param (backwards compat)', () => {
      const content = '[ACTION:limit_order] inputMint=SOL outputMint=USDC amount=10 price=105';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('limit_order');
      // "price" is stored as-is by parser; normalizeParams in solana-action.service maps it to targetPrice
      expect(result[0].params['price']).toBe('105');
    });

    it('should parse limit_order with expirySeconds', () => {
      const content = '[ACTION:limit_order] inputMint=SOL outputMint=USDC amount=5 targetPrice=200 expirySeconds=86400';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].params['expirySeconds']).toBe('86400');
    });

    it('should parse cancel_limit_order with "order" param', () => {
      const content = '[ACTION:cancel_limit_order] order=ABC123DEF456';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('cancel_limit_order');
      expect(result[0].params['order']).toBe('ABC123DEF456');
    });

    it('should parse cancel_limit_order with legacy "orderId" param (backwards compat)', () => {
      const content = '[ACTION:cancel_limit_order] orderId=ABC123DEF456';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('cancel_limit_order');
      // "orderId" stored as-is; normalizeParams maps it to "order"
      expect(result[0].params['orderId']).toBe('ABC123DEF456');
    });

    it('should parse cancel_all_limit_orders with no params', () => {
      const content = 'Cancel all my limit orders [ACTION:cancel_all_limit_orders]';
      const result = service.parse(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('cancel_all_limit_orders');
    });
  });

  // =====================
  // QUERY PARSING TESTS
  // =====================

  describe('parseQueries() - Query parsing', () => {
    it('should parse balance query', () => {
      const content = 'Show my balance [QUERY:balance] wallet=self token=all';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('balance');
      expect(result[0].params['wallet']).toBe('self');
      expect(result[0].params['token']).toBe('all');
    });

    it('should parse price query', () => {
      const content = 'What is SOL price? [QUERY:price] token=SOL vs=usd';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('price');
      expect(result[0].params['token']).toBe('SOL');
      expect(result[0].params['vs']).toBe('usd');
    });

    it('should filter unknown query types', () => {
      const content = '[QUERY:unknown_query] foo=bar';
      const result = service.parseQueries(content);

      expect(result.length).toBe(0);
    });

    it('should parse multiple queries', () => {
      const content = `
        [QUERY:balance] wallet=self
        [QUERY:price] token=SOL
      `;
      const result = service.parseQueries(content);

      expect(result.length).toBe(2);
      expect(result[0].type).toBe('balance');
      expect(result[1].type).toBe('price');
    });

    it('should parse portfolio query', () => {
      const content = 'Show my portfolio [QUERY:portfolio] wallet=self';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('portfolio');
    });

    it('should parse positions query with protocol filter', () => {
      const content = 'Show my staking positions [QUERY:positions] wallet=self protocol=marinade';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('positions');
      expect(result[0].params['protocol']).toBe('marinade');
    });

    // ---- Limit orders query tests ----

    it('should parse limit_orders query with wallet=self', () => {
      const content = 'Show my limit orders [QUERY:limit_orders] wallet=self';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('limit_orders');
      expect(result[0].params['wallet']).toBe('self');
    });

    it('should parse limit_orders query with status=history', () => {
      const content = '[QUERY:limit_orders] wallet=self status=history';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].type).toBe('limit_orders');
      expect(result[0].params['status']).toBe('history');
    });

    it('should parse limit_orders query with status=open (default)', () => {
      const content = '[QUERY:limit_orders] wallet=self status=open';
      const result = service.parseQueries(content);

      expect(result.length).toBe(1);
      expect(result[0].params['status']).toBe('open');
    });
  });

  // =====================
  // CLARIFY PARSING TESTS
  // =====================

  describe('parseClarifications() - Clarify block parsing', () => {
    it('should parse clarify blocks', () => {
      const content = `
        [CLARIFY:swap] {"question": "Which token do you want to swap to?", "options": [{"label": "USDC", "action": "swap", "params": {"outputMint": "USDC"}}, {"label": "USDT", "action": "swap", "params": {"outputMint": "USDT"}}]}
      `;
      const result = service.parseClarifications(content);

      expect(result.length).toBe(1);
      expect(result[0].category).toBe('swap');
      expect(result[0].question).toBe('Which token do you want to swap to?');
      expect(result[0].options.length).toBe(2);
    });
  });

  // =====================
  // PARSE ALL TESTS
  // =====================

  describe('parseAll() - Combined parsing', () => {
    it('should parse actions, queries, and clarifications together', () => {
      const content = `
        Swap my SOL to USDC and check my balance.
        [ACTION:swap] {"inputMint": "SOL", "outputMint": "USDC", "amount": "1"}
        [QUERY:balance] wallet=self token=all
        [CLARIFY:swap] {"question": "Which DEX?", "options": [{"label": "Jupiter", "action": "swap", "params": {}}]}
      `;
      const result = service.parseAll(content);

      expect(result.actions.length).toBe(1);
      expect(result.queries.length).toBe(1);
      expect(result.clarifications.length).toBe(1);
    });

    it('should handle empty content', () => {
      const result = service.parseAll('');
      expect(result.actions).toEqual([]);
      expect(result.queries).toEqual([]);
      expect(result.clarifications).toEqual([]);
    });
  });

  // =====================
  // HAS CHECK TESTS
  // =====================

  describe('hasActions() and hasQueries()', () => {
    it('should detect actions in content', () => {
      expect(service.hasActions('[ACTION:swap] foo=bar')).toBe(true);
      expect(service.hasActions('Just some text')).toBe(false);
    });

    it('should detect queries in content', () => {
      expect(service.hasQueries('[QUERY:balance] wallet=self')).toBe(true);
      expect(service.hasQueries('Just some text')).toBe(false);
    });
  });
});
