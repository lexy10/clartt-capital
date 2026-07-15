import { IsArray, Matches } from 'class-validator';

// Match a UUID by SHAPE only (8-4-4-4-12 hex), not by version. Some seeded
// strategies carry hand-crafted IDs whose version nibble isn't 4
// (e.g. f1a2b3c4-d5e6-7890-abcd-ef1234500025), so @IsUUID('4') wrongly
// rejected valid, existing strategy IDs — assigning them returned 400.
const UUID_SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class SetAccountStrategiesDto {
  @IsArray()
  @Matches(UUID_SHAPE, { each: true, message: 'each strategyId must be a UUID' })
  strategyIds: string[];
}
