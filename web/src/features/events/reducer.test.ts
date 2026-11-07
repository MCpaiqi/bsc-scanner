import { describe, expect, it } from "vitest";
import { initialEventState, reduceRadarEvent } from "./reducer";

const candidate = { chain: "bsc", token_address: "0xabc", symbol: "ABC", launchpad: "unknown", priority_score: 10, features: { risk: "LOW" }, data_status: "OBSERVED", evidence: {}, latest_signal_class: "NONE", latest_signal_score: 0 };
describe("radar event reducer", () => {
  it("retains canonical rows when an unversioned late hint carries different financial values", () => {
    const state = { ...initialEventState, candidates: [candidate] };
    const hint = { event_id: "late", event_type: "CANDIDATE_UPDATED" as const, chain: "bsc", token: "0xabc", occurred_at: "now", data: { priority_score: 999, features: { risk: "HIGH" } } };
    const next = reduceRadarEvent(state, hint);
    expect(next.candidates).toBe(state.candidates);
    expect(next.lastEvent).toBe(hint);
  });
  it("records risk hints without modifying rows", () => {
    const state = { ...initialEventState, candidates: [candidate] };
    const next = reduceRadarEvent(state, { event_id: "1", event_type: "RISK_CHANGED", chain: "bsc", token: "0xabc", occurred_at: "now", data: { features: { risk: "HIGH" } } });
    expect(next.candidates).toHaveLength(1);
    expect(next.candidates[0].features.risk).toBe("LOW");
    expect(next.lastEvent?.event_type).toBe("RISK_CHANGED");
  });
  it("preserves canonical values when an update arrives", () => {
    const state = { ...initialEventState, candidates: [candidate] };
    const next = reduceRadarEvent(state, { event_id: "2", event_type: "CANDIDATE_UPDATED", chain: "bsc", token: "0xabc", occurred_at: "now", data: { priority_score: 42, features: { liquidity: 1200 } } });
    expect(next.candidates[0].priority_score).toBe(10);
    expect(next.candidates[0].features).toEqual({ risk: "LOW" });
  });
  it("retains the latest signal hint without inventing a current signal", () => {
    const state = { ...initialEventState, candidates: [candidate] };
    const risk = reduceRadarEvent(state, { event_id: "3", event_type: "RISK_CHANGED", chain: "bsc", token: "0xabc", occurred_at: "now", data: { risk: "HIGH" } });
    const signal = reduceRadarEvent(risk, { event_id: "4", event_type: "SIGNAL_CHANGED", chain: "bsc", token: "0xabc", occurred_at: "now", data: { signal: "BUY", signal_score: 81 } });
    expect(signal.candidates[0].features.risk).toBe("LOW");
    expect(signal.candidates[0].latest_signal_class).toBe("NONE");
    expect(signal.candidates[0].latest_signal_score).toBe(0);
  });
});
