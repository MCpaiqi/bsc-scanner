import { afterEach, describe, expect, it, vi } from "vitest";
import { api, connectRadarEvents } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("radar API parsing", () => {
  it("parses the canonical candidate envelope without inventing missing fields", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ candidates: [{ token_address: "0x1", features: { risk: "UNKNOWN" } }], count: 1, total: 1 }), { status: 200 })));
    const payload = await api.candidates(new URLSearchParams({ limit: "1" }));
    expect(payload.count).toBe(1);
    expect(payload.candidates[0].token_address).toBe("0x1");
    expect(payload.candidates[0].features.liquidity).toBeUndefined();
    vi.unstubAllGlobals();
  });
});

describe("radar event connection", () => {
  it("tracks successful opens and reconnects without waiting for a business event", () => {
    class FakeEventSource extends EventTarget {
      static instance: FakeEventSource;
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      close = vi.fn();
      constructor(readonly url: string) { super(); FakeEventSource.instance = this; }
    }
    vi.stubGlobal("EventSource", FakeEventSource);
    const onEvent = vi.fn(), onError = vi.fn(), onOpen = vi.fn();
    const disconnect = connectRadarEvents(onEvent, onError, onOpen);
    const source = FakeEventSource.instance;
    expect(source.url).toContain("/api/radar/events?event_types=CANDIDATE_DISCOVERED");
    source.onopen?.();
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onEvent).not.toHaveBeenCalled();
    source.onerror?.();
    expect(onError).toHaveBeenCalledTimes(1);
    source.onopen?.();
    expect(onOpen).toHaveBeenCalledTimes(2);
    const event = { event_id: "new", event_type: "CANDIDATE_UPDATED", chain: "bsc", token: "0x1", occurred_at: "2026-09-30T12:00:00Z", data: {} };
    source.dispatchEvent(new MessageEvent("CANDIDATE_UPDATED", { data: JSON.stringify(event) }));
    expect(onEvent).toHaveBeenCalledWith(event);
    disconnect();
    expect(source.onopen).toBeNull();
    expect(source.onerror).toBeNull();
    expect(source.close).toHaveBeenCalledOnce();
    source.dispatchEvent(new MessageEvent("CANDIDATE_UPDATED", { data: JSON.stringify(event) }));
    expect(onEvent).toHaveBeenCalledOnce();
  });
});

describe("bounded recovery controls", () => {
  class Source extends EventTarget {
    static current: Source;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    close() {}
    constructor() { super(); Source.current = this; }
  }
  const event = { event_id: "stable", event_type: "CANDIDATE_UPDATED", chain: "bsc", token: "token", occurred_at: "2026-01-01T00:00:00Z", data: {} };
  it("deduplicates retry hints and consumes reset as recovery rather than market data", () => {
    vi.stubGlobal("EventSource", Source);
    const onEvent = vi.fn(), onReset = vi.fn();
    const close = connectRadarEvents(onEvent, undefined, undefined, onReset);
    for (let i = 0; i < 2; i++) Source.current.dispatchEvent(new MessageEvent("CANDIDATE_UPDATED", { data: JSON.stringify(event) }));
    expect(onEvent).toHaveBeenCalledOnce();
    const data = { reason: "REPLAY_LIMIT", resume_from: "tail", live_available: true };
    Source.current.dispatchEvent(new MessageEvent("STREAM_RESET", { data: JSON.stringify({ event_id: "reset", event_type: "STREAM_RESET", data }) }));
    expect(onReset).toHaveBeenCalledWith(data);
    expect(onEvent).toHaveBeenCalledOnce();
    close();
  });
  it("requests resnapshot when a business frame cannot be interpreted", () => {
    vi.stubGlobal("EventSource", Source);
    const onEvent = vi.fn(), onReset = vi.fn();
    const close = connectRadarEvents(onEvent, undefined, undefined, onReset);
    Source.current.dispatchEvent(new MessageEvent("CANDIDATE_UPDATED", { data: "not-json" }));
    expect(onReset).toHaveBeenCalledWith(expect.objectContaining({ reason: "INVALID_EVENT" }));
    expect(onEvent).not.toHaveBeenCalled();
    close();
  });
});
