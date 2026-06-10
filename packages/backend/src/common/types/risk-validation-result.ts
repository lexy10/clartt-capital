export type RiskRuleName =
  | 'max_risk_per_trade'
  | 'daily_loss_limit'
  | 'max_positions'
  | 'max_lot_exposure'
  | 'spread_check'
  | 'slippage_check';

export interface RiskRuleResult {
  rule: RiskRuleName;
  passed: boolean;
  reason?: string;
}

export interface RiskValidationResult {
  approved: boolean;
  signal_id: string;
  account_id: string;
  rules_checked: RiskRuleResult[];
  rejected_by?: RiskRuleName;
  validated_at: string;          // ISO 8601
}
