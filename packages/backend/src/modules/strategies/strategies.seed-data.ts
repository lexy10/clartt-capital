// AUTO-GENERATED from the working dev DB. The canonical strategy catalogue
// shipped to a fresh deployment. Regenerate by exporting the strategies table:
//   SELECT json_agg(row_to_json(s)) FROM strategies s;
// then drop created_by/timestamps and reformat. Edited strategies in a live
// DB are NOT overwritten by the seed (it inserts only missing ids).
//
// NOTE: "V25 Trend Continuation Scalper" ships disabled — its stored config
// is incomplete (algorithm_params only, missing instruments/timeframes/risk),
// so it would fail engine validation. The row exists so it can be finished in
// the dashboard; enable it once its config is complete.

export interface StrategySeed {
  id: string;
  name: string;
  algorithm: string;
  enabled: boolean;
  config: Record<string, unknown>;
}

export const STRATEGY_SEED_DATA: StrategySeed[] = [
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234501000",
    "name": "Boom 1000 Spike Rider",
    "algorithm": "boom_crash_spike",
    "enabled": false,
    "config": {
      "mode": "forward_test",
      "enabled": false,
      "exit_rules": {
        "time_exit": {
          "enabled": true,
          "max_duration_minutes": 240
        },
        "break_even": {
          "enabled": false
        },
        "partial_close": {
          "enabled": false
        },
        "trailing_stop": {
          "enabled": false
        },
        "atr_trailing_stop": {
          "enabled": false
        }
      },
      "timeframes": [
        "15m",
        "5m"
      ],
      "instruments": [
        "BOOM_1000"
      ],
      "risk_settings": {
        "max_spread": 10.0,
        "max_slippage": 5.0,
        "max_daily_loss_pct": 15.0,
        "min_reward_risk_ratio": 1.5,
        "volatility_multiplier": 3.0,
        "max_risk_per_trade_pct": 5.0,
        "max_trailing_drawdown_pct": 30.0
      },
      "entry_timeframe": "5m",
      "session_windows": [],
      "trend_timeframe": "1h",
      "algorithm_params": {
        "spike_atr_mult": 3.0,
        "spike_lookback": 10,
        "reward_risk_ratio": 2.0,
        "post_spike_cooldown": 3,
        "instrument_direction": "boom"
      },
      "higher_timeframe": "15m",
      "min_confidence_score": 0.5,
      "news_protection_minutes": 0
    }
  },
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234500010",
    "name": "V10 Range Sniper",
    "algorithm": "v10_range_sniper",
    "enabled": false,
    "config": {
      "mode": "forward_test",
      "enabled": false,
      "exit_rules": {
        "time_exit": {
          "enabled": true,
          "max_duration_minutes": 120
        },
        "break_even": {
          "enabled": false
        },
        "partial_close": {
          "enabled": false
        },
        "trailing_stop": {
          "enabled": false
        },
        "atr_trailing_stop": {
          "enabled": false
        }
      },
      "timeframes": [
        "15m",
        "5m"
      ],
      "instruments": [
        "R_10"
      ],
      "risk_settings": {
        "max_spread": 5.0,
        "max_slippage": 3.0,
        "max_daily_loss_pct": 20.0,
        "min_reward_risk_ratio": 1.0,
        "volatility_multiplier": 3.0,
        "max_risk_per_trade_pct": 10.0,
        "max_trailing_drawdown_pct": 40.0
      },
      "entry_timeframe": "5m",
      "session_windows": [],
      "trend_timeframe": "1h",
      "algorithm_params": {
        "fractal_n": 2,
        "require_bos": false,
        "stoch_d_smooth": 3,
        "stoch_k_period": 10,
        "stoch_k_smooth": 3,
        "stoch_oversold": 20,
        "trend_lookback": 30,
        "cooldown_candles": 1,
        "stoch_overbought": 80,
        "reward_risk_ratio": 1.5,
        "structure_lookback": 80,
        "cooldown_price_atr_mult": 0.5
      },
      "higher_timeframe": "15m",
      "min_confidence_score": 0.5,
      "news_protection_minutes": 0
    }
  },
  {
    "id": "84433980-f7cb-497e-af3d-3f434b22179d",
    "name": "V25 Scalper",
    "algorithm": "fractal_structure",
    "enabled": true,
    "config": {
      "mode": "live",
      "enabled": true,
      "exit_rules": {
        "time_exit": {
          "enabled": false,
          "max_duration_minutes": 240
        },
        "break_even": {
          "enabled": false,
          "buffer_pips": 2,
          "activation_pips": 15
        },
        "partial_close": {
          "enabled": false,
          "trigger_pips": 30,
          "close_percent": 50
        },
        "trailing_stop": {
          "enabled": false,
          "activation_pips": 20,
          "trail_distance_pips": 10
        },
        "atr_trailing_stop": {
          "enabled": true,
          "trail_atr_mult": 1,
          "activation_atr_mult": 1.5
        }
      },
      "timeframes": [
        "5m",
        "1h",
        "4h"
      ],
      "instruments": [
        "R_25"
      ],
      "risk_settings": {
        "max_spread": 50,
        "max_slippage": 10,
        "max_daily_loss_pct": 4,
        "min_reward_risk_ratio": 1.5,
        "volatility_multiplier": 1.5,
        "max_risk_per_trade_pct": 1,
        "max_trailing_drawdown_pct": 10
      },
      "entry_timeframe": "5m",
      "trend_timeframe": "4h",
      "algorithm_params": {
        "fractal_n": 2,
        "atr_period": 14,
        "htf_lookback": 80,
        "min_rr_ratio": 2,
        "entry_lookback": 50,
        "trend_lookback": 80,
        "atr_buffer_mult": 0.5,
        "min_sl_atr_mult": 1,
        "cooldown_candles": 5,
        "structure_lookback": 200,
        "entry_fractal_recency": 10,
        "h1_confluence_lookback": 20
      },
      "higher_timeframe": "1h",
      "min_confidence_score": 0.45
    }
  },
  {
    "id": "f1a2b3c4-d5e6-7890-abcd-ef1234500025",
    "name": "V25 Structure Scalper",
    "algorithm": "v25_structure_scalper",
    "enabled": true,
    "config": {
      "mode": "live",
      "enabled": true,
      "exit_rules": {
        "time_exit": {
          "enabled": false,
          "max_duration_minutes": 9999
        },
        "break_even": {
          "enabled": false,
          "buffer_pips": 1,
          "activation_pips": 9999
        },
        "partial_close": {
          "enabled": false,
          "trigger_pips": 9999,
          "close_percent": 50
        },
        "trailing_stop": {
          "enabled": false,
          "activation_pips": 999,
          "trail_distance_pips": 99
        },
        "atr_trailing_stop": {
          "enabled": false,
          "trail_atr_mult": 9,
          "activation_atr_mult": 9
        },
        "structural_trailing_stop": {
          "enabled": false
        }
      },
      "timeframes": [
        "1h",
        "5m"
      ],
      "instruments": [
        "R_25"
      ],
      "risk_settings": {
        "max_spread": 5,
        "max_slippage": 3,
        "max_daily_loss_pct": 25,
        "min_reward_risk_ratio": 2,
        "volatility_multiplier": 3,
        "max_risk_per_trade_pct": 5,
        "max_trailing_drawdown_pct": 50
      },
      "entry_timeframe": "5m",
      "session_windows": [],
      "trend_timeframe": "4h",
      "algorithm_params": {
        "fractal_n": 2,
        "atr_period": 14,
        "max_sl_atr": 3,
        "min_sl_atr": 0.5,
        "require_bos": false,
        "min_bos_count": 1,
        "sl_atr_buffer": 0.3,
        "stoch_d_smooth": 3,
        "stoch_k_period": 14,
        "stoch_k_smooth": 3,
        "stoch_oversold": 40,
        "trend_lookback": 30,
        "sl_swing_source": "structure",
        "cooldown_candles": 0,
        "stoch_overbought": 60,
        "reward_risk_ratio": 3,
        "structure_lookback": 120,
        "atr_lookback_window": 50,
        "require_stoch_cross": false,
        "use_premium_discount": false,
        "require_confirm_candle": false,
        "cooldown_price_atr_mult": 0.5
      },
      "higher_timeframe": "1h",
      "min_confidence_score": 0.45,
      "news_protection_minutes": 0
    }
  },
  {
    "id": "ba4dbaf1-5a73-46aa-ab59-7b274e37d50d",
    "name": "V25 Trend Continuation Scalper",
    "algorithm": "v25_trend_continuation_scalper",
    "enabled": false,
    "config": {
      "algorithm_params": {
        "structure_lookback": 400
      }
    }
  },
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234500075",
    "name": "V75 Momentum Rider",
    "algorithm": "v75_momentum_rider",
    "enabled": true,
    "config": {
      "mode": "forward_test",
      "enabled": true,
      "exit_rules": {
        "time_exit": {
          "enabled": true,
          "max_duration_minutes": 960
        },
        "break_even": {
          "enabled": false
        },
        "partial_close": {
          "enabled": false
        },
        "trailing_stop": {
          "enabled": false
        },
        "atr_trailing_stop": {
          "enabled": false
        }
      },
      "timeframes": [
        "1h",
        "15m"
      ],
      "instruments": [
        "R_75"
      ],
      "risk_settings": {
        "max_spread": 15.0,
        "max_slippage": 10.0,
        "max_daily_loss_pct": 10.0,
        "min_reward_risk_ratio": 3.0,
        "volatility_multiplier": 3.0,
        "max_risk_per_trade_pct": 3.0,
        "max_trailing_drawdown_pct": 30.0
      },
      "entry_timeframe": "15m",
      "session_windows": [],
      "trend_timeframe": "4h",
      "algorithm_params": {
        "fractal_n": 2,
        "atr_period": 14,
        "ema_period": 21,
        "max_sl_atr": 0.5,
        "min_sl_atr": 0.3,
        "rsi_period": 14,
        "require_bos": false,
        "max_atr_ratio": 3.5,
        "min_atr_ratio": 0.3,
        "rsi_bear_ceil": 55,
        "sl_atr_buffer": 0.3,
        "rsi_bull_floor": 45,
        "stoch_d_smooth": 3,
        "stoch_k_period": 14,
        "stoch_k_smooth": 3,
        "stoch_oversold": 30,
        "trend_lookback": 30,
        "base_confidence": 0.6,
        "cooldown_candles": 1,
        "stoch_overbought": 70,
        "reward_risk_ratio": 3.0,
        "require_rsi_filter": false,
        "structure_lookback": 100,
        "atr_lookback_window": 50,
        "pullback_atr_tolerance": 0.5,
        "require_confirm_candle": true,
        "cooldown_price_atr_mult": 0.8
      },
      "higher_timeframe": "1h",
      "min_confidence_score": 0.5,
      "news_protection_minutes": 0
    }
  },
  {
    "id": "8768076d-e99c-451e-9303-205d9e706255",
    "name": "V75 Scalper",
    "algorithm": "fractal_structure",
    "enabled": true,
    "config": {
      "mode": "live",
      "enabled": true,
      "exit_rules": {
        "time_exit": {
          "enabled": false,
          "max_duration_minutes": 240
        },
        "break_even": {
          "enabled": false,
          "buffer_pips": 2,
          "activation_pips": 15
        },
        "partial_close": {
          "enabled": false,
          "trigger_pips": 30,
          "close_percent": 50
        },
        "trailing_stop": {
          "enabled": false,
          "activation_pips": 20,
          "trail_distance_pips": 10
        },
        "atr_trailing_stop": {
          "enabled": true,
          "trail_atr_mult": 1.5,
          "activation_atr_mult": 2
        }
      },
      "timeframes": [
        "5m",
        "1h",
        "4h"
      ],
      "instruments": [
        "R_75"
      ],
      "risk_settings": {
        "max_spread": 100,
        "max_slippage": 20,
        "max_daily_loss_pct": 4,
        "min_reward_risk_ratio": 1.5,
        "volatility_multiplier": 2,
        "max_risk_per_trade_pct": 1,
        "max_trailing_drawdown_pct": 10
      },
      "entry_timeframe": "5m",
      "trend_timeframe": "4h",
      "algorithm_params": {
        "fractal_n": 2,
        "atr_period": 14,
        "min_rr_ratio": 2,
        "trend_lookback": 100,
        "atr_buffer_mult": 0.5,
        "cooldown_candles": 3,
        "pullback_max_pct": 0.9,
        "pullback_min_pct": 0.2,
        "structure_lookback": 200,
        "require_entry_fractal": false,
        "min_swings_for_structure": 3
      },
      "higher_timeframe": "1h",
      "min_confidence_score": 0.45
    }
  }
];
