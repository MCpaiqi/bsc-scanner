import type { Candidate, RadarEvent } from "../../types/radar";

export interface EventState { candidates: Candidate[]; selectedToken: string | null; lastEvent: RadarEvent | null; }
export const initialEventState: EventState = { candidates: [], selectedToken: null, lastEvent: null };

// Events can arrive late or twice; only REST snapshots may replace research rows.
export function reduceRadarEvent(state: EventState, event: RadarEvent): EventState {
  return { ...state, lastEvent: event };
}
