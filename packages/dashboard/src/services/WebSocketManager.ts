import { io, Socket } from 'socket.io-client';
import type { Candle } from '../types/candle';
import type { RawSignal, Signal } from '../types/signal';
import { normalizeSignal } from '../types/signal';
import type { TradeExecutionResult } from '../types/trade';
import type { AlertTriggered, KillSwitchStatus } from '../types/websocket';
import type {
  AutopilotStateChangeEvent,
  StrategyOverlayEvent,
  TradeEntryEvent,
  TradeExitEvent,
} from '../types/autopilot';
import type { BacktestUpdateEvent } from '../types/api';
import { useConnectionStore } from '../stores/connectionStore';
import { useAutopilotStore } from '../stores/autopilotStore';

import type { TradingEvent } from '../types/event';

export type WSChannel = 'candles' | 'signals' | 'trades' | 'alerts' | 'kill_switch' | 'autopilot' | 'backtest' | 'account_sync' | 'events' | 'reconciliation_discrepancy' | 'circuitBreaker_stateChange' | 'consumerLag_alert';

export interface AccountSyncEvent {
  syncedAt: string;
}

type ChannelDataMap = {
  candles: Candle;
  signals: Signal;
  trades: TradeExecutionResult;
  alerts: AlertTriggered;
  kill_switch: KillSwitchStatus;
  autopilot: AutopilotStateChangeEvent | StrategyOverlayEvent | TradeEntryEvent | TradeExitEvent;
  backtest: BacktestUpdateEvent;
  account_sync: AccountSyncEvent;
  events: TradingEvent;
  reconciliation_discrepancy: unknown;
  circuitBreaker_stateChange: unknown;
  consumerLag_alert: unknown;
};

type ChannelCallback<C extends WSChannel> = (data: ChannelDataMap[C]) => void;

interface SubscriptionEntry {
  channel: WSChannel;
  callback: ChannelCallback<WSChannel>;
}

const INITIAL_RECONNECT_DELAY = 1000;
const MAX_RECONNECT_DELAY = 30000;
const BACKOFF_MULTIPLIER = 2;

export class WebSocketManager {
  private socket: Socket | null = null;
  private subscriptions: Map<string, SubscriptionEntry> = new Map();
  private reconnectDelay = INITIAL_RECONNECT_DELAY;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private url: string;
  private manualDisconnect = false;
  private subIdCounter = 0;
  private reconnectCallbacks: Map<string, () => void> = new Map();
  private reconnectCbCounter = 0;

  constructor(url?: string) {
    this.url = url ?? import.meta.env.VITE_WS_URL ?? '';
    // Reconnect immediately when the tab regains focus or the network returns.
    // Backgrounded tabs get their socket suspended and their reconnect timers
    // throttled, so without this the UI can sit on "Reconnecting" until a
    // manual reload.
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', this.handleVisibility);
    }
    if (typeof window !== 'undefined') {
      window.addEventListener('online', this.handleOnline);
    }
  }

  private handleVisibility = (): void => {
    if (document.visibilityState === 'visible') this.wakeReconnect();
  };

  private handleOnline = (): void => {
    this.wakeReconnect();
  };

  /** Force an immediate, fresh reconnect if we're not connected. Resets the
   *  backoff so a returning user doesn't wait out a 30s timer. */
  private wakeReconnect(): void {
    if (this.manualDisconnect) return;
    if (this.socket?.connected) return;
    this.reconnectDelay = INITIAL_RECONNECT_DELAY;
    this.clearReconnectTimer();
    this.recreateAndConnect();
  }

  /** Tear down the old socket and build a new one. A new socket re-reads the
   *  auth token from localStorage at creation, so reconnects pick up a token
   *  that was refreshed while the tab was inactive — reusing the old socket
   *  would keep retrying with the stale token and loop forever. */
  private recreateAndConnect(): void {
    if (this.socket) {
      this.socket.removeAllListeners();
      this.socket.disconnect();
      this.socket = null;
    }
    this.connect();
  }

  connect(): void {
    if (this.socket?.connected) return;

    this.manualDisconnect = false;
    this.clearReconnectTimer();

    this.socket = io(this.url, {
      transports: ['websocket'],
      autoConnect: false,
      reconnection: false, // we handle reconnection manually
      // The gateway rejects unauthenticated sockets. Read the token at
      // connect() time (a new socket is created per attempt, so reconnects
      // automatically pick up a refreshed token from localStorage).
      auth: { token: localStorage.getItem('us30_access_token') ?? '' },
    });

    this.socket.on('connect', this.handleConnect);
    this.socket.on('disconnect', this.handleDisconnect);
    this.socket.on('connect_error', this.handleError);

    // Register event listeners for each channel
    this.socket.on('candleUpdate', (data: Candle) => this.dispatch('candles', data));
    this.socket.on('signal', (data: RawSignal | Signal) => this.dispatch('signals', normalizeSignal(data as RawSignal)));
    this.socket.on('trade', (data: TradeExecutionResult) => this.dispatch('trades', data));
    this.socket.on('alert', (data: AlertTriggered) => this.dispatch('alerts', data));
    this.socket.on('kill_switch', (data: KillSwitchStatus) => this.dispatch('kill_switch', data));

    // Autopilot channel events — dispatch to store directly
    this.socket.on('autopilot:state_change', (data: AutopilotStateChangeEvent) => {
      useAutopilotStore.getState().setEnabled(data.enabled);
      this.dispatch('autopilot', data);
    });
    this.socket.on('autopilot:strategy_overlay', (data: StrategyOverlayEvent) => {
      const store = useAutopilotStore.getState();
      for (const overlay of data.overlays) {
        store.addOverlay(overlay as import('../types/autopilot').OverlayData);
      }
      this.dispatch('autopilot', data);
    });
    this.socket.on('autopilot:trade_entry', (data: TradeEntryEvent) => {
      useAutopilotStore.getState().addTradeMarker({
        id: data.trade.id,
        signalId: data.trade.signalId,
        direction: data.trade.direction,
        entryPrice: data.trade.entryPrice,
        executedAt: data.trade.executedAt,
        type: 'entry',
      });
      this.dispatch('autopilot', data);
    });
    this.socket.on('autopilot:trade_exit', (data: TradeExitEvent) => {
      useAutopilotStore.getState().addTradeMarker({
        id: data.trade.id,
        signalId: data.trade.signalId,
        direction: data.trade.direction,
        entryPrice: data.trade.entryPrice,
        exitPrice: data.trade.exitPrice,
        profitLoss: data.trade.profitLoss,
        exitReason: data.trade.exitReason,
        executedAt: data.trade.executedAt,
        type: 'exit',
      });
      this.dispatch('autopilot', data);
    });

    // Backtest channel
    this.socket.on('backtest:update', (data: BacktestUpdateEvent) => {
      this.dispatch('backtest', data);
    });

    // Account sync channel
    this.socket.on('account:sync', (data: AccountSyncEvent) => {
      this.dispatch('account_sync', data);
    });

    // Events channel
    this.socket.on('event:new', (data: TradingEvent) => {
      this.dispatch('events', data);
    });

    this.socket.connect();
    useConnectionStore.getState().setStatus('reconnecting');
  }

  disconnect(): void {
    this.manualDisconnect = true;
    this.clearReconnectTimer();
    if (this.socket) {
      this.socket.removeAllListeners();
      this.socket.disconnect();
      this.socket = null;
    }
    useConnectionStore.getState().setStatus('disconnected');
  }

  subscribe<C extends WSChannel>(channel: C, callback: ChannelCallback<C>): string {
    const id = String(++this.subIdCounter);
    this.subscriptions.set(id, {
      channel,
      callback: callback as ChannelCallback<WSChannel>,
    });
    return id;
  }

  unsubscribe(id: string): void {
    this.subscriptions.delete(id);
  }

  /** Emit an event to the server (e.g., to join a room) */
  emit(event: string, data?: unknown): void {
    if (this.socket?.connected) {
      this.socket.emit(event, data);
    }
  }

  /** Register a callback to run on every reconnect. Returns an id for cleanup. */
  onReconnect(callback: () => void): string {
    const id = String(++this.reconnectCbCounter);
    this.reconnectCallbacks.set(id, callback);
    return id;
  }

  /** Remove a reconnect callback */
  offReconnect(id: string): void {
    this.reconnectCallbacks.delete(id);
  }

  private dispatch<C extends WSChannel>(channel: C, data: ChannelDataMap[C]): void {
    for (const entry of this.subscriptions.values()) {
      if (entry.channel === channel) {
        entry.callback(data as ChannelDataMap[typeof entry.channel]);
      }
    }
  }

  private handleConnect = (): void => {
    this.reconnectDelay = INITIAL_RECONNECT_DELAY;
    useConnectionStore.getState().setStatus('connected');

    // Subscribe to autopilot channel using userId from JWT
    if (this.socket) {
      const userId = this.getUserIdFromToken();
      if (userId) {
        this.socket.emit('subscribeAutopilot', { userId });
      }
      this.socket.emit('subscribeBacktest');
    }

    // Fire all registered reconnect callbacks (e.g., re-subscribe candles)
    for (const cb of this.reconnectCallbacks.values()) {
      try {
        cb();
      } catch {
        // Swallow errors in reconnect callbacks to avoid breaking the chain
      }
    }
  };

  private getUserIdFromToken(): string | null {
    try {
      const token = localStorage.getItem('us30_access_token');
      if (!token) return null;
      const payload = JSON.parse(atob(token.split('.')[1]));
      return payload.sub ?? payload.userId ?? null;
    } catch {
      return null;
    }
  }

  private handleDisconnect = (): void => {
    useConnectionStore.getState().setStatus('disconnected');
    if (!this.manualDisconnect) {
      this.scheduleReconnect();
    }
  };

  private handleError = (): void => {
    if (!this.manualDisconnect) {
      this.scheduleReconnect();
    }
  };

  private scheduleReconnect(): void {
    this.clearReconnectTimer();
    useConnectionStore.getState().setStatus('reconnecting');

    this.reconnectTimer = setTimeout(() => {
      this.reconnectDelay = Math.min(
        this.reconnectDelay * BACKOFF_MULTIPLIER,
        MAX_RECONNECT_DELAY,
      );
      // Rebuild the socket so it re-reads a possibly-refreshed auth token.
      // (Reusing this.socket kept retrying with the token baked in at creation.)
      this.recreateAndConnect();
    }, this.reconnectDelay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

// Singleton instance
export const wsManager = new WebSocketManager();
