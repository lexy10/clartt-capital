-- Seed 10 trading accounts for the demo user
DO $$
DECLARE
  uid UUID := 'f64d9e85-3311-4652-9cac-0fd8fa328a71';
  acc_ids UUID[];
  acc UUID;
  sig_id UUID;
  trade_id UUID;
  i INT;
  j INT;
  labels TEXT[] := ARRAY['US30 Scalper', 'Gold Swing', 'US30 Momentum', 'XAUUSD Breakout', 'Conservative Fund', 'Aggressive Growth', 'Hedging Account', 'Prop Firm Challenge', 'Long-Term Portfolio', 'Day Trading Lab'];
  balances NUMERIC[] := ARRAY[25000, 50000, 10000, 75000, 100000, 15000, 30000, 200000, 45000, 8000];
  instruments TEXT[];
  directions TEXT[];
  entry_p NUMERIC;
  exit_p NUMERIC;
  sl_p NUMERIC;
  tp_p NUMERIC;
  pnl NUMERIC;
  lot NUMERIC;
  snap_equity NUMERIC;
  snap_balance NUMERIC;
  snap_unrealized NUMERIC;
  trade_opened TIMESTAMPTZ;
  trade_closed TIMESTAMPTZ;
BEGIN
  -- Create 10 accounts
  FOR i IN 1..10 LOOP
    acc := gen_random_uuid();
    acc_ids := array_append(acc_ids, acc);
    INSERT INTO trading_accounts (id, user_id, label, is_active, metaapi_account_id, mt5_login, mt5_server)
    VALUES (acc, uid, labels[i], true, 'meta-' || i, '500' || (1000 + i)::text, 'ICMarkets-Demo');
  END LOOP;

  -- For each account, create signals, trades, and snapshots
  FOR i IN 1..10 LOOP
    acc := acc_ids[i];
    snap_balance := balances[i];
    snap_equity := snap_balance;

    -- Create 8-15 trades per account over the past month
    FOR j IN 1..(8 + (i % 8)) LOOP
      -- Alternate instruments
      IF j % 3 = 0 THEN
        instruments := ARRAY['XAUUSD'];
      ELSE
        instruments := ARRAY['US30'];
      END IF;

      -- Alternate directions
      IF j % 2 = 0 THEN
        directions := ARRAY['BUY'];
      ELSE
        directions := ARRAY['SELL'];
      END IF;

      -- Generate signal
      sig_id := gen_random_uuid();
      IF instruments[1] = 'XAUUSD' THEN
        entry_p := 2650.00 + (random() * 50)::numeric(18,2);
        sl_p := entry_p - 5.00 - (random() * 10)::numeric(18,2);
        tp_p := entry_p + 8.00 + (random() * 15)::numeric(18,2);
      ELSE
        entry_p := 42500.00 + (random() * 1000)::numeric(18,2);
        sl_p := entry_p - 50.00 - (random() * 100)::numeric(18,2);
        tp_p := entry_p + 80.00 + (random() * 150)::numeric(18,2);
      END IF;

      INSERT INTO signals (id, instrument, direction, entry_price, stop_loss, take_profit, position_size, confidence_score, timeframe, mode)
      VALUES (sig_id, instruments[1], directions[1], entry_p, sl_p, tp_p, 0.5 + (random() * 2)::numeric(18,2), 0.75 + (random() * 0.2)::numeric(5,4), '15m', 'live');

      -- Generate trade
      trade_id := gen_random_uuid();
      lot := 0.1 + (random() * 1.5)::numeric(18,2);
      trade_opened := NOW() - ((random() * 25 + 1)::int || ' days')::interval - ((random() * 12)::int || ' hours')::interval;
      trade_closed := trade_opened + ((random() * 4 + 0.5)::numeric || ' hours')::interval;

      -- Randomize P&L: ~60% winners
      IF random() < 0.6 THEN
        pnl := (random() * 800 + 50)::numeric(18,2);
        IF directions[1] = 'BUY' THEN
          exit_p := entry_p + (random() * 30 + 5)::numeric(18,2);
        ELSE
          exit_p := entry_p - (random() * 30 + 5)::numeric(18,2);
        END IF;
      ELSE
        pnl := -1 * (random() * 500 + 20)::numeric(18,2);
        IF directions[1] = 'BUY' THEN
          exit_p := entry_p - (random() * 20 + 3)::numeric(18,2);
        ELSE
          exit_p := entry_p + (random() * 20 + 3)::numeric(18,2);
        END IF;
      END IF;

      INSERT INTO trades (id, signal_id, account_id, direction, entry_price, exit_price, position_size, profit_loss, status, opened_at, closed_at)
      VALUES (trade_id, sig_id, acc, directions[1], entry_p, exit_p, lot, pnl, 'closed', trade_opened, trade_closed);

      snap_equity := snap_equity + pnl;
    END LOOP;

    -- Create portfolio snapshots (daily for past 30 days)
    snap_equity := balances[i];
    FOR j IN 0..29 LOOP
      snap_unrealized := (random() * 200 - 100)::numeric(18,2);
      snap_equity := snap_equity + (random() * 400 - 150)::numeric(18,2);
      snap_balance := snap_equity - snap_unrealized;

      INSERT INTO portfolio_snapshots (id, account_id, equity, balance, unrealized_pnl, open_positions, snapshot_at)
      VALUES (
        gen_random_uuid(),
        acc,
        snap_equity,
        snap_balance,
        snap_unrealized,
        (random() * 3)::int,
        NOW() - ((30 - j) || ' days')::interval + '12:00:00'::interval
      );
    END LOOP;
  END LOOP;
END $$;


-- Seed strategies for the demo user
INSERT INTO strategies (id, name, algorithm, config, created_by, created_at, updated_at) VALUES
(
  'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
  'US30 Order Block Scalper',
  'ict_order_block',
  '{"instruments":["US30"],"timeframes":["1m","5m","15m"],"higher_timeframe":"1h","entry_timeframe":"5m","mode":"live","news_protection_minutes":30,"min_confidence_score":0.65,"algorithm_params":{},"session_windows":[{"name":"London","start_hour":8,"start_minute":0,"end_hour":12,"end_minute":0},{"name":"New York","start_hour":13,"start_minute":30,"end_hour":20,"end_minute":0}],"risk_settings":{"max_risk_per_trade_pct":1.0,"max_daily_loss_pct":3.0,"max_spread":5.0,"max_slippage":3.0,"volatility_multiplier":2.0}}'::jsonb,
  'f64d9e85-3311-4652-9cac-0fd8fa328a71',
  NOW() - INTERVAL '14 days',
  NOW() - INTERVAL '2 days'
),
(
  'b2c3d4e5-f6a7-8901-bcde-f12345678901',
  'US30 Momentum Breakout',
  'ict_order_block',
  '{"instruments":["US30"],"timeframes":["5m","15m","1h"],"higher_timeframe":"4h","entry_timeframe":"15m","mode":"live","news_protection_minutes":45,"min_confidence_score":0.70,"algorithm_params":{},"session_windows":[{"name":"New York Open","start_hour":13,"start_minute":30,"end_hour":16,"end_minute":0}],"risk_settings":{"max_risk_per_trade_pct":1.5,"max_daily_loss_pct":4.0,"max_spread":4.0,"max_slippage":2.5,"volatility_multiplier":2.5}}'::jsonb,
  'f64d9e85-3311-4652-9cac-0fd8fa328a71',
  NOW() - INTERVAL '10 days',
  NOW() - INTERVAL '1 day'
),
(
  'c3d4e5f6-a7b8-9012-cdef-123456789012',
  'XAUUSD Swing Trader',
  'ict_order_block',
  '{"instruments":["XAUUSD"],"timeframes":["15m","1h","4h"],"higher_timeframe":"1d","entry_timeframe":"1h","mode":"live","news_protection_minutes":60,"min_confidence_score":0.75,"algorithm_params":{},"session_windows":[{"name":"London","start_hour":8,"start_minute":0,"end_hour":16,"end_minute":0},{"name":"New York","start_hour":13,"start_minute":30,"end_hour":20,"end_minute":0}],"risk_settings":{"max_risk_per_trade_pct":0.75,"max_daily_loss_pct":2.5,"max_spread":3.5,"max_slippage":2.0,"volatility_multiplier":1.8}}'::jsonb,
  'f64d9e85-3311-4652-9cac-0fd8fa328a71',
  NOW() - INTERVAL '7 days',
  NOW() - INTERVAL '12 hours'
),
(
  'd4e5f6a7-b8c9-0123-defa-234567890123',
  'US30 London Session Scalper',
  'ict_order_block',
  '{"instruments":["US30"],"timeframes":["1m","5m"],"higher_timeframe":"15m","entry_timeframe":"1m","mode":"live","news_protection_minutes":15,"min_confidence_score":0.60,"algorithm_params":{},"session_windows":[{"name":"London","start_hour":8,"start_minute":0,"end_hour":11,"end_minute":30}],"risk_settings":{"max_risk_per_trade_pct":0.5,"max_daily_loss_pct":2.0,"max_spread":3.0,"max_slippage":1.5,"volatility_multiplier":1.5}}'::jsonb,
  'f64d9e85-3311-4652-9cac-0fd8fa328a71',
  NOW() - INTERVAL '5 days',
  NOW() - INTERVAL '6 hours'
),
(
  'e5f6a7b8-c9d0-1234-efab-345678901234',
  'XAUUSD News Fade',
  'ict_order_block',
  '{"instruments":["XAUUSD"],"timeframes":["5m","15m"],"higher_timeframe":"1h","entry_timeframe":"5m","mode":"forward_test","news_protection_minutes":0,"min_confidence_score":0.80,"algorithm_params":{},"session_windows":[{"name":"US Data Release","start_hour":13,"start_minute":0,"end_hour":15,"end_minute":0}],"risk_settings":{"max_risk_per_trade_pct":0.5,"max_daily_loss_pct":1.5,"max_spread":5.0,"max_slippage":4.0,"volatility_multiplier":3.0}}'::jsonb,
  'f64d9e85-3311-4652-9cac-0fd8fa328a71',
  NOW() - INTERVAL '3 days',
  NOW() - INTERVAL '3 hours'
)
ON CONFLICT (id) DO NOTHING;
