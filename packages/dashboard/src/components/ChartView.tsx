import { useEffect, useRef, useState, useCallback, type FC } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts';
import { wsManager } from '../services/WebSocketManager';
import { useChartStore } from '../stores/chartStore';
import { useAutopilotStore } from '../stores/autopilotStore';
import type { Candle } from '../types/candle';
import type { Signal } from '../types/signal';
import type { Timeframe } from '../types/timeframe';
import type { OverlayDirection, TradeMarker as AutopilotTradeMarker } from '../types/autopilot';
import { useThemeStore } from '../stores/themeStore';

/** Read the active theme's chart colours from CSS variables (falls back to the
 *  dark palette if a variable is missing). Lets the chart follow light/dark. */
function readChartTheme() {
  const s = typeof document !== 'undefined' ? getComputedStyle(document.documentElement) : null;
  const v = (name: string, fallback: string) => {
    const raw = s?.getPropertyValue(name).trim();
    return raw ? raw : fallback;
  };
  return {
    bg: v('--chart-bg', '#0d1117'),
    text: v('--text-secondary', '#8b949e'),
    grid: v('--chart-grid', '#1c2128'),
    crosshair: v('--chart-crosshair', '#484f58'),
    border: v('--border-primary', '#30363d'),
    up: v('--chart-candle-up', '#3fb950'),
    down: v('--chart-candle-down', '#f85149'),
    labelBg: v('--bg-surface', '#21262d'),
  };
}

interface OrderBlockZone {
  id: string;
  direction: 'bullish' | 'bearish';
  high: number;
  low: number;
  startTime: string;
  endTime?: string;
}

interface TradeMarker {
  time: string;
  type: 'entry' | 'exit';
  direction: 'BUY' | 'SELL';
  price: number;
}

interface ChartViewProps {
  instrument?: string;
  timeframe?: Timeframe;
  orderBlocks?: OrderBlockZone[];
  signals?: Signal[];
  tradeMarkers?: TradeMarker[];
}

function toChartTime(iso: string): Time {
  return Math.floor(new Date(iso).getTime() / 1000) as Time;
}

function candleToChartData(candle: Candle): CandlestickData<Time> {
  return {
    time: toChartTime(candle.timestamp),
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
  };
}

function directionColor(direction: OverlayDirection, alpha: number): string {
  switch (direction) {
    case 'bullish':
      return `rgba(63, 185, 80, ${alpha})`;
    case 'bearish':
      return `rgba(248, 81, 73, ${alpha})`;
    case 'neutral':
      return `rgba(88, 166, 255, ${alpha})`;
  }
}

const ChartView: FC<ChartViewProps> = ({
  instrument: instrumentProp,
  timeframe: timeframeProp,
  orderBlocks = [],
  signals = [],
  tradeMarkers = [],
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const obSeriesRef = useRef<ISeriesApi<'Line'>[]>([]);
  // Track autopilot overlay price lines for cleanup
  const autopilotLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);
  // Track autopilot trade connecting lines for cleanup
  const tradeConnectLinesRef = useRef<ReturnType<ISeriesApi<'Candlestick'>['createPriceLine']>[]>([]);
  // Tooltip state for trade marker hover
  const [tooltip, setTooltip] = useState<{
    visible: boolean;
    x: number;
    y: number;
    content: {
      direction: string;
      entryPrice: number;
      exitPrice?: number;
      profitLoss?: number;
      timestamp: string;
    };
  } | null>(null);

  const storeInstrument = useChartStore((s) => s.instrument);
  const storeTimeframe = useChartStore((s) => s.timeframe);
  const candles = useChartStore((s) => s.candles);
  const addCandle = useChartStore((s) => s.addCandle);
  const setFitContentFn = useChartStore((s) => s.setFitContentFn);
  const fetchCandles = useChartStore((s) => s.fetchCandles);

  const instrument = instrumentProp ?? storeInstrument;
  const timeframe = timeframeProp ?? storeTimeframe;

  // Autopilot store selectors
  const autopilotOverlays = useAutopilotStore((s) => s.overlays);
  const overlayVisibility = useAutopilotStore((s) => s.overlayVisibility);
  const autopilotTradeMarkers = useAutopilotStore((s) => s.tradeMarkers);

  // Initialize chart once (no timeframe dependency — avoids full recreation flicker)
  useEffect(() => {
    if (!containerRef.current) return;

    const t = readChartTheme();
    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: t.bg },
        textColor: t.text,
        fontFamily:
          "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
      },
      grid: {
        vertLines: { color: t.grid },
        horzLines: { color: t.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: t.crosshair, labelBackgroundColor: t.labelBg },
        horzLine: { color: t.crosshair, labelBackgroundColor: t.labelBg },
      },
      rightPriceScale: {
        borderColor: t.border,
      },
      timeScale: {
        borderColor: t.border,
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const series = chart.addCandlestickSeries({
      upColor: t.up,
      downColor: t.down,
      borderUpColor: t.up,
      borderDownColor: t.down,
      wickUpColor: t.up,
      wickDownColor: t.down,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Expose fitContent to the store so toolbar can trigger it
    // Uses a TradingView-style auto: show recent ~150 bars, not squeeze all data
    const fitFn = () => {
      if (!seriesRef.current) {
        chart.timeScale().fitContent();
        return;
      }
      const data = useChartStore.getState().candles;
      if (data.length === 0) return;
      const VISIBLE_BARS = 150;
      if (data.length > VISIBLE_BARS) {
        const mapped = data.map(candleToChartData);
        const from = mapped[mapped.length - VISIBLE_BARS].time;
        const to = mapped[mapped.length - 1].time;
        chart.timeScale().setVisibleRange({ from, to });
      } else {
        chart.timeScale().fitContent();
      }
    };
    setFitContentFn(fitFn);

    // Responsive resize
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        chart.applyOptions({ width, height });
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      setFitContentFn(null);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      obSeriesRef.current = [];
    };
  }, []);

  // Update time scale options when timeframe changes (no chart recreation)
  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.applyOptions({
      timeScale: { secondsVisible: false },
    });
  }, [timeframe]);

  // Recolour the chart live when the user switches light/dark or accent.
  const themeMode = useThemeStore((s) => s.mode);
  const themeAccent = useThemeStore((s) => s.accent);
  useEffect(() => {
    if (!chartRef.current) return;
    const t = readChartTheme();
    chartRef.current.applyOptions({
      layout: { background: { type: ColorType.Solid, color: t.bg }, textColor: t.text },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      crosshair: {
        vertLine: { color: t.crosshair, labelBackgroundColor: t.labelBg },
        horzLine: { color: t.crosshair, labelBackgroundColor: t.labelBg },
      },
      rightPriceScale: { borderColor: t.border },
      timeScale: { borderColor: t.border },
    });
    seriesRef.current?.applyOptions({
      upColor: t.up, downColor: t.down,
      borderUpColor: t.up, borderDownColor: t.down,
      wickUpColor: t.up, wickDownColor: t.down,
    });
  }, [themeMode, themeAccent]);

  // Track the number of candles from the last setData call so we can
  // distinguish a full REST fetch (many candles) from a single WebSocket
  // tick that arrives before the fetch completes.
  const lastSetDataCountRef = useRef(0);

  useEffect(() => {
    if (!seriesRef.current || !chartRef.current || candles.length === 0) return;

    // Always do setData when the candle count jumps significantly (REST
    // fetch completed) or on the very first load.  Skip when the count
    // only grew by 0-1 (live WebSocket tick) — series.update() in the
    // WS handler already covers that case without resetting the viewport.
    const delta = candles.length - lastSetDataCountRef.current;
    const isInitialOrBulkLoad = lastSetDataCountRef.current === 0 || delta > 1;

    if (isInitialOrBulkLoad) {
      const data = candles.map(candleToChartData);
      seriesRef.current.setData(data);

      const VISIBLE_BARS = 150;
      if (data.length > VISIBLE_BARS) {
        const from = data[data.length - VISIBLE_BARS].time;
        const to = data[data.length - 1].time;
        chartRef.current.timeScale().setVisibleRange({ from, to });
      } else {
        chartRef.current.timeScale().fitContent();
      }
    }

    lastSetDataCountRef.current = candles.length;
  }, [candles]);

  // Reset the counter when instrument or timeframe changes so the next
  // fetchCandles triggers a full setData + zoom
  useEffect(() => {
    lastSetDataCountRef.current = 0;
  }, [instrument, timeframe]);

  // Subscribe to WebSocket candle updates — only use series.update() for live ticks
  // This updates the chart in-place without resetting the visible range
  useEffect(() => {
    wsManager.emit('subscribeCandles', { instrument, timeframe });

    const subId = wsManager.subscribe('candles', (candle: Candle) => {
      if (candle.instrument === instrument && candle.timeframe === timeframe) {
        addCandle(candle);
        if (seriesRef.current) {
          seriesRef.current.update(candleToChartData(candle));
        }
      }
    });

    return () => {
      wsManager.unsubscribe(subId);
    };
  }, [instrument, timeframe, addCandle]);

  // Re-subscribe to candle room and refetch data on WebSocket reconnect
  useEffect(() => {
    const reconnectId = wsManager.onReconnect(() => {
      wsManager.emit('subscribeCandles', { instrument, timeframe });
      fetchCandles();
    });

    return () => {
      wsManager.offReconnect(reconnectId);
    };
  }, [instrument, timeframe, fetchCandles]);

  // Render order block zones as price lines (highlighted regions)
  const renderOrderBlocks = useCallback(() => {
    const series = seriesRef.current;
    if (!series) return;

    // Remove old price lines by recreating — lightweight-charts doesn't have
    // a bulk-remove, so we track them and remove individually.
    obSeriesRef.current = [];

    for (const ob of orderBlocks) {
      const color =
        ob.direction === 'bullish'
          ? 'rgba(63, 185, 80, 0.12)'
          : 'rgba(248, 81, 73, 0.12)';
      const lineColor =
        ob.direction === 'bullish'
          ? 'rgba(63, 185, 80, 0.5)'
          : 'rgba(248, 81, 73, 0.5)';

      // Use price lines to mark the high and low of the order block zone
      series.createPriceLine({
        price: ob.high,
        color: lineColor,
        lineWidth: 1,
        lineStyle: 2, // Dashed
        axisLabelVisible: false,
        title: ob.direction === 'bullish' ? 'OB↑' : 'OB↓',
      });
      series.createPriceLine({
        price: ob.low,
        color: color,
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: false,
        title: '',
      });
    }
  }, [orderBlocks]);

  useEffect(() => {
    renderOrderBlocks();
  }, [renderOrderBlocks]);

  // Render signal markers and trade entry/exit markers (including autopilot trade markers)
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    const markers: SeriesMarker<Time>[] = [];
    const signalList = Array.isArray(signals) ? signals : [];
    const tradeMarkerList = Array.isArray(tradeMarkers) ? tradeMarkers : [];
    const autopilotTradeMarkerList = Array.isArray(autopilotTradeMarkers) ? autopilotTradeMarkers : [];

    // Signal markers
    for (const signal of signalList) {
      markers.push({
        time: toChartTime(signal.created_at),
        position: signal.direction === 'BUY' ? 'belowBar' : 'aboveBar',
        shape: signal.direction === 'BUY' ? 'arrowUp' : 'arrowDown',
        color: signal.direction === 'BUY' ? '#3fb950' : '#f85149',
        text: `${signal.direction} ${signal.confidence_score.toFixed(2)}`,
      });
    }

    // Trade entry/exit markers (prop-based)
    for (const tm of tradeMarkerList) {
      const isEntry = tm.type === 'entry';
      markers.push({
        time: toChartTime(tm.time),
        position: tm.direction === 'BUY' ? 'belowBar' : 'aboveBar',
        shape: isEntry ? 'circle' : 'square',
        color: isEntry ? '#58a6ff' : '#d29922',
        text: isEntry ? 'Entry' : 'Exit',
      });
    }

    // Autopilot trade markers from store
    for (const atm of autopilotTradeMarkerList) {
      if (atm.type === 'entry') {
        // Entry markers: buy = arrowUp below bar (green), sell = arrowDown above bar (red)
        markers.push({
          time: toChartTime(atm.executedAt),
          position: atm.direction === 'BUY' ? 'belowBar' : 'aboveBar',
          shape: atm.direction === 'BUY' ? 'arrowUp' : 'arrowDown',
          color: atm.direction === 'BUY' ? '#3fb950' : '#f85149',
          text: `${atm.direction} @${atm.entryPrice.toFixed(1)}`,
        });
      } else {
        // Exit markers: profit = green square, loss = red square
        const isProfit = (atm.profitLoss ?? 0) > 0;
        markers.push({
          time: toChartTime(atm.executedAt),
          position: atm.direction === 'BUY' ? 'aboveBar' : 'belowBar',
          shape: 'square',
          color: isProfit ? '#3fb950' : '#f85149',
          text: `Exit @${(atm.exitPrice ?? atm.entryPrice).toFixed(1)}`,
        });
      }
    }

    // Sort markers by time (required by lightweight-charts)
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    series.setMarkers(markers);
  }, [signals, tradeMarkers, autopilotTradeMarkers]);

  // Render autopilot strategy overlays (entry zones, exit zones, order blocks)
  // Batched in a single useEffect so all overlays update within one rendering frame
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    // Clean up previous autopilot price lines
    for (const line of autopilotLinesRef.current) {
      series.removePriceLine(line);
    }
    autopilotLinesRef.current = [];

    // Order block overlays — semi-transparent colored rectangles via price line pairs
    if (overlayVisibility.orderBlocks) {
      for (const ob of autopilotOverlays.orderBlocks) {
        const lineColor = directionColor(ob.direction, 0.5);
        const bandColor = directionColor(ob.direction, 0.12);

        autopilotLinesRef.current.push(
          series.createPriceLine({
            price: ob.priceHigh,
            color: lineColor,
            lineWidth: 1,
            lineStyle: 0, // Solid
            axisLabelVisible: false,
            title: ob.direction === 'bullish' ? 'OB↑' : ob.direction === 'bearish' ? 'OB↓' : 'OB',
          }),
        );
        autopilotLinesRef.current.push(
          series.createPriceLine({
            price: ob.priceLow,
            color: bandColor,
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: false,
            title: '',
          }),
        );
      }
    }

    // Entry zones — colored horizontal bands (price line pairs for priceHigh/priceLow)
    if (overlayVisibility.entryZones) {
      for (const ez of autopilotOverlays.entryZones) {
        const color = directionColor(ez.direction, 0.35);

        autopilotLinesRef.current.push(
          series.createPriceLine({
            price: ez.priceHigh,
            color,
            lineWidth: 2,
            lineStyle: 0, // Solid
            axisLabelVisible: false,
            title: 'Entry',
          }),
        );
        autopilotLinesRef.current.push(
          series.createPriceLine({
            price: ez.priceLow,
            color,
            lineWidth: 2,
            lineStyle: 0,
            axisLabelVisible: false,
            title: '',
          }),
        );
      }
    }

    // Exit zones — dashed horizontal lines (SL = red, TP = green)
    if (overlayVisibility.exitZones) {
      for (const ex of autopilotOverlays.exitZones) {
        const isSL = ex.type === 'stop_loss';
        const color = isSL ? '#f85149' : '#3fb950';
        const title = isSL ? 'SL' : 'TP';

        autopilotLinesRef.current.push(
          series.createPriceLine({
            price: ex.price,
            color,
            lineWidth: 1,
            lineStyle: 2, // Dashed
            axisLabelVisible: true,
            title,
          }),
        );
      }
    }
  }, [autopilotOverlays, overlayVisibility]);

  // Render connecting lines between matched autopilot entry/exit trade pairs
  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;

    // Clean up previous connecting lines
    for (const line of tradeConnectLinesRef.current) {
      series.removePriceLine(line);
    }
    tradeConnectLinesRef.current = [];

    // Build a map of entry markers by signalId
    const entryBySignal = new Map<string, AutopilotTradeMarker>();
    for (const m of autopilotTradeMarkers) {
      if (m.type === 'entry') {
        entryBySignal.set(m.signalId, m);
      }
    }

    // For each exit marker, find its matching entry and draw a connecting price line
    for (const exitMarker of autopilotTradeMarkers) {
      if (exitMarker.type !== 'exit') continue;
      const entryMarker = entryBySignal.get(exitMarker.signalId);
      if (!entryMarker) continue;

      const isProfit = (exitMarker.profitLoss ?? 0) > 0;
      const color = isProfit ? '#3fb950' : '#f85149';

      // Draw a line at the entry price
      tradeConnectLinesRef.current.push(
        series.createPriceLine({
          price: entryMarker.entryPrice,
          color,
          lineWidth: 1,
          lineStyle: 1, // Dotted
          axisLabelVisible: false,
          title: '',
        }),
      );

      // Draw a line at the exit price
      tradeConnectLinesRef.current.push(
        series.createPriceLine({
          price: exitMarker.exitPrice ?? exitMarker.entryPrice,
          color,
          lineWidth: 1,
          lineStyle: 1, // Dotted
          axisLabelVisible: false,
          title: '',
        }),
      );
    }
  }, [autopilotTradeMarkers]);

  // Tooltip on crosshair hover over autopilot trade markers
  useEffect(() => {
    const chart = chartRef.current;
    const series = seriesRef.current;
    if (!chart || !series || autopilotTradeMarkers.length === 0) return;

    const handler = (param: {
      point?: { x: number; y: number };
      time?: Time;
    }) => {
      if (!param.point || !param.time) {
        setTooltip(null);
        return;
      }

      const hoverTime = param.time as number;
      // Find the closest autopilot trade marker within a small time threshold
      const THRESHOLD = 60; // seconds tolerance
      let closest: AutopilotTradeMarker | null = null;
      let closestDist = Infinity;

      for (const m of autopilotTradeMarkers) {
        const mTime = Math.floor(new Date(m.executedAt).getTime() / 1000);
        const dist = Math.abs(mTime - hoverTime);
        if (dist < closestDist && dist <= THRESHOLD) {
          closestDist = dist;
          closest = m;
        }
      }

      if (closest) {
        setTooltip({
          visible: true,
          x: param.point.x,
          y: param.point.y,
          content: {
            direction: closest.direction,
            entryPrice: closest.entryPrice,
            exitPrice: closest.exitPrice,
            profitLoss: closest.profitLoss,
            timestamp: closest.executedAt,
          },
        });
      } else {
        setTooltip(null);
      }
    };

    chart.subscribeCrosshairMove(handler);
    return () => {
      chart.unsubscribeCrosshairMove(handler);
    };
  }, [autopilotTradeMarkers]);

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <div
        ref={containerRef}
        style={{ width: '100%', height: '100%' }}
      />
      {tooltip?.visible && (
        <div
          className="autopilot-trade-tooltip"
          style={{
            position: 'absolute',
            left: tooltip.x + 16,
            top: tooltip.y + 16,
            background: 'rgba(22, 27, 34, 0.92)',
            backdropFilter: 'blur(8px)',
            border: '1px solid #30363d',
            borderRadius: '8px',
            padding: '8px 12px',
            color: '#e6edf3',
            fontSize: '12px',
            fontFamily: "'Inter', 'Segoe UI', sans-serif",
            pointerEvents: 'none',
            zIndex: 100,
            whiteSpace: 'nowrap',
            lineHeight: 1.6,
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 2 }}>
            {tooltip.content.direction}{' '}
            <span style={{ color: '#8b949e' }}>Trade</span>
          </div>
          <div>Entry: {tooltip.content.entryPrice.toFixed(2)}</div>
          {tooltip.content.exitPrice != null && (
            <div>Exit: {tooltip.content.exitPrice.toFixed(2)}</div>
          )}
          {tooltip.content.profitLoss != null && (
            <div
              style={{
                color:
                  tooltip.content.profitLoss > 0 ? '#3fb950' : '#f85149',
                fontWeight: 600,
              }}
            >
              P/L: {tooltip.content.profitLoss > 0 ? '+' : ''}
              {tooltip.content.profitLoss.toFixed(2)}
            </div>
          )}
          <div style={{ color: '#8b949e', fontSize: '11px' }}>
            {new Date(tooltip.content.timestamp).toLocaleString()}
          </div>
        </div>
      )}
    </div>
  );
};

export default ChartView;
