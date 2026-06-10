export interface Tick {
  instrument: string;
  price: number;
  volume: number;
  timestamp: string; // ISO 8601
}
