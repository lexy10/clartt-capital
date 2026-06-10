export type StructureType = 'higher_high' | 'higher_low' | 'lower_high' | 'lower_low';

export interface StructurePoint {
  type: StructureType;
  price: number;
  timestamp: string; // ISO 8601
  candle_index: number;
}

export type BOSDirection = 'bullish' | 'bearish';

export interface BOS {
  direction: BOSDirection;
  break_price: number;
  break_timestamp: string; // ISO 8601
  from_point: StructurePoint;
  to_point: StructurePoint;
}

export interface OrderBlock {
  id: string;                    // UUID
  instrument: string;
  direction: BOSDirection;
  zone_high: number;
  zone_low: number;
  formation_timestamp: string;   // ISO 8601
  bos_id?: string;
  is_valid: boolean;
}
