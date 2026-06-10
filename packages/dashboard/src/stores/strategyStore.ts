import { create } from 'zustand';
import type { Strategy, BacktestConfig, BacktestResult, BacktestUpdateEvent, AlgorithmInfo } from '../types/api';
import { apiClient } from '../services/ApiClient';

interface StrategyState {
  strategies: Strategy[];
  loading: boolean;
  error: string | null;
  selectedStrategyId: string | null;

  algorithms: AlgorithmInfo[];
  algorithmsLoading: boolean;

  backtestLoading: boolean;
  backtestError: string | null;
  lastBacktestResult: BacktestResult | null;
  backtestHistory: BacktestResult[];
  backtestHistoryLoading: boolean;
  backtestsByStrategy: Record<string, BacktestResult[]>;

  fetchStrategies: () => Promise<void>;
  fetchAlgorithms: () => Promise<void>;
  createStrategy: (dto: { name: string; algorithm?: string; config: Record<string, unknown> }) => Promise<void>;
  updateStrategy: (id: string, dto: { name?: string; algorithm?: string; config?: Record<string, unknown>; enabled?: boolean }) => Promise<void>;
  deleteStrategy: (id: string) => Promise<void>;
  selectStrategy: (id: string | null) => void;
  clearSelection: () => void;
  runBacktest: (config: BacktestConfig) => Promise<void>;
  clearBacktestResult: () => void;
  fetchBacktestHistory: (strategyId: string) => Promise<void>;
  updateBacktestFromWS: (payload: BacktestUpdateEvent) => void;
}

export const useStrategyStore = create<StrategyState>((set) => ({
  strategies: [],
  loading: false,
  error: null,
  selectedStrategyId: null,

  algorithms: [],
  algorithmsLoading: false,

  backtestLoading: false,
  backtestError: null,
  lastBacktestResult: null,
  backtestHistory: [],
  backtestHistoryLoading: false,
  backtestsByStrategy: {},

  fetchStrategies: async () => {
    set({ loading: true, error: null });
    try {
      const strategies = await apiClient.strategies.list();
      set({ strategies, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch strategies';
      set({ error: message, loading: false });
    }
  },

  fetchAlgorithms: async () => {
    set({ algorithmsLoading: true });
    try {
      const algorithms = await apiClient.strategies.getAlgorithms();
      set({ algorithms, algorithmsLoading: false });
    } catch {
      set({ algorithms: [], algorithmsLoading: false });
    }
  },

  updateStrategy: async (id: string, dto: { name?: string; algorithm?: string; config?: Record<string, unknown>; enabled?: boolean }) => {
    const updated = await apiClient.strategies.update(id, dto);
    set((state) => ({
      strategies: state.strategies.map((s) => (s.id === id ? updated : s)),
    }));
  },

  createStrategy: async (dto: { name: string; algorithm?: string; config: Record<string, unknown> }) => {
    const created = await apiClient.strategies.create(dto);
    set((state) => ({
      strategies: [created, ...state.strategies],
    }));
  },

  deleteStrategy: async (id: string) => {
    await apiClient.strategies.remove(id);
    set((state) => ({
      strategies: state.strategies.filter((s) => s.id !== id),
      selectedStrategyId: state.selectedStrategyId === id ? null : state.selectedStrategyId,
    }));
  },

  selectStrategy: (id: string | null) => {
    set({ selectedStrategyId: id });
  },

  clearSelection: () => {
    set({ selectedStrategyId: null });
  },

  runBacktest: async (config: BacktestConfig) => {
    set({ backtestLoading: true, backtestError: null });
    try {
      const result = await apiClient.strategies.runBacktest(config);
      const sid = result.strategy_id ?? result.strategyId ?? config.strategy_id;
      set((state) => ({
        lastBacktestResult: result,
        backtestLoading: false,
        backtestsByStrategy: {
          ...state.backtestsByStrategy,
          [sid]: [
            result,
            ...(state.backtestsByStrategy[sid] ?? []),
          ],
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to run backtest';
      set({ backtestError: message, backtestLoading: false });
    }
  },

  clearBacktestResult: () => {
    set({ lastBacktestResult: null, backtestError: null });
  },

  fetchBacktestHistory: async (strategyId: string) => {
    set({ backtestHistoryLoading: true });
    try {
      const backtestHistory = await apiClient.strategies.getBacktestResults(strategyId);
      set((state) => ({
        backtestHistory,
        backtestHistoryLoading: false,
        backtestsByStrategy: {
          ...state.backtestsByStrategy,
          [strategyId]: backtestHistory,
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to fetch backtest history';
      set({ backtestError: message, backtestHistoryLoading: false });
    }
  },

  updateBacktestFromWS: (payload: BacktestUpdateEvent) => {
    set((state) => {
      const strategyBacktests = state.backtestsByStrategy[payload.strategy_id] ?? [];
      const updatedBacktests = strategyBacktests.map((bt) => {
        if (bt.id !== payload.result_id) return bt;
        return {
          ...bt,
          status: payload.status,
          win_rate: payload.win_rate ?? bt.win_rate,
          max_drawdown: payload.max_drawdown ?? bt.max_drawdown,
          sharpe_ratio: payload.sharpe_ratio ?? bt.sharpe_ratio,
          profit_factor: payload.profit_factor ?? bt.profit_factor,
          expectancy: payload.expectancy ?? bt.expectancy,
          total_trades: payload.total_trades ?? bt.total_trades,
          winning_trades: payload.winning_trades ?? bt.winning_trades,
          losing_trades: payload.losing_trades ?? bt.losing_trades,
          gross_profit: payload.gross_profit ?? bt.gross_profit,
          gross_loss: payload.gross_loss ?? bt.gross_loss,
          net_profit: payload.net_profit ?? bt.net_profit,
          equity_curve: payload.equity_curve ?? bt.equity_curve,
          trade_results: payload.trade_results ?? bt.trade_results,
          error_message: payload.error_message ?? bt.error_message,
        };
      });

      // Also update lastBacktestResult if it matches
      let updatedLast = state.lastBacktestResult;
      if (updatedLast && updatedLast.id === payload.result_id) {
        updatedLast = {
          ...updatedLast,
          status: payload.status,
          win_rate: payload.win_rate ?? updatedLast.win_rate,
          winRate: payload.win_rate ?? updatedLast.winRate,
          max_drawdown: payload.max_drawdown ?? updatedLast.max_drawdown,
          maxDrawdown: payload.max_drawdown ?? updatedLast.maxDrawdown,
          sharpe_ratio: payload.sharpe_ratio ?? updatedLast.sharpe_ratio,
          sharpeRatio: payload.sharpe_ratio ?? updatedLast.sharpeRatio,
          profit_factor: payload.profit_factor ?? updatedLast.profit_factor,
          profitFactor: payload.profit_factor ?? updatedLast.profitFactor,
          expectancy: payload.expectancy ?? updatedLast.expectancy,
          total_trades: payload.total_trades ?? updatedLast.total_trades,
          totalTrades: payload.total_trades ?? updatedLast.totalTrades,
          winning_trades: payload.winning_trades ?? updatedLast.winning_trades,
          winningTrades: payload.winning_trades ?? updatedLast.winningTrades,
          losing_trades: payload.losing_trades ?? updatedLast.losing_trades,
          losingTrades: payload.losing_trades ?? updatedLast.losingTrades,
          gross_profit: payload.gross_profit ?? updatedLast.gross_profit,
          grossProfit: payload.gross_profit ?? updatedLast.grossProfit,
          gross_loss: payload.gross_loss ?? updatedLast.gross_loss,
          grossLoss: payload.gross_loss ?? updatedLast.grossLoss,
          net_profit: payload.net_profit ?? updatedLast.net_profit,
          netProfit: payload.net_profit ?? updatedLast.netProfit,
          equity_curve: payload.equity_curve ?? updatedLast.equity_curve,
          equityCurve: payload.equity_curve ?? updatedLast.equityCurve,
          trade_results: payload.trade_results ?? updatedLast.trade_results,
          tradeResults: payload.trade_results ?? updatedLast.tradeResults,
          error_message: payload.error_message ?? updatedLast.error_message,
          errorMessage: payload.error_message ?? updatedLast.errorMessage,
        };
      }

      return {
        lastBacktestResult: updatedLast,
        backtestsByStrategy: {
          ...state.backtestsByStrategy,
          [payload.strategy_id]: updatedBacktests,
        },
      };
    });
  },
}));
