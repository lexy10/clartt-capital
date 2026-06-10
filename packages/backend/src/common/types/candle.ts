import { Timeframe } from './timeframe';

export interface Candle {
  instrument: string;
  timeframe: Timeframe;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  timestamp: string; // ISO 8601
}
