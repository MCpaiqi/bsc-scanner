import { afterEach, describe, expect, it, vi } from "vitest";
import { api, connectRadarEvents } from "./client";

afterEach(() => { api.lockAnalyst?.(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("memory-only analyst credentials", () => {
  const key = "synthetic-frontend-credential-" + "a".repeat(32);
  const status = (authorized: boolean) => new Response(JSON.stringify({ enabled: true, authorized }), { status: 200 });
  it("sends credentials only to validation and mutation requests without persisting them", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem");
    const fetcher = vi.fn().mockImplementation((path: string) => Promise.resolve(
      path === "/api/radar/access" ? status(true) : new Response(JSON.stringify({ revision: 1 }))));
    vi.stubGlobal("fetch", fetcher);
    expect((await api.analystAccess(key)).authorized).toBe(true);
    await api.health();
    await api.saveAnnotation("bsc", "0x1", { expected_revision: 0 });
    expect(fetcher.mock.calls[0][0]).toBe("/api/radar/access");
    expect(new Headers(fetcher.mock.calls[0][1].headers).get("Authorization")).toBe(`Bearer ${key}`);
    expect(new Headers(fetcher.mock.calls[1][1].headers).has("Authorization")).toBe(false);
    expect(new Headers(fetcher.mock.calls[2][1].headers).get("Authorization")).toBe(`Bearer ${key}`);
    expect(fetcher.mock.calls[2][1]).toMatchObject({ redirect: "error", credentials: "omit" });
    expect(storage).not.toHaveBeenCalled();
    expect(fetcher.mock.calls.every(([url]) => !String(url).includes(key))).toBe(true);
    api.lockAnalyst();
    await expect(api.saveAnnotation("bsc", "0x1", {})).rejects.toBeInstanceOf(Error);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("cannot reauthorize after a late validation response following lock", async () => {
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn().mockReturnValueOnce(new Promise<Response>(done => { resolve = done; }));
    vi.stubGlobal("fetch", fetcher);
    const pending = api.analystAccess(key);
    api.lockAnalyst(); resolve(status(true));
    expect((await pending).authorized).toBe(false);
    await expect(api.createAlertRule({})).rejects.toBeInstanceOf(Error);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("rejects credential transmission over a remote HTTP origin", async () => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    vi.stubGlobal("location", { protocol: "http:", hostname: "research.example" });
    await expect(api.analystAccess(key)).rejects.toMatchObject({ code: "ANALYST_INSECURE_TRANSPORT" });
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("clears authorization after an actual credential rejection", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(status(true)).mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: { code: "ANALYST_UNAUTHORIZED" } }), { status: 401 }));
    vi.stubGlobal("fetch", fetcher);
    await api.analystAccess(key);
    await expect(api.saveAnnotation("bsc", "0x1", {})).rejects.toMatchObject({ status: 401 });
    await expect(api.saveAnnotation("bsc", "0x1", {})).rejects.toMatchObject({ status: 401 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("cannot clear a new authorization with a late rejection of an older write", async () => {
    let rejectOld!: (value: Response) => void;
    let writes = 0;
    const newer = "synthetic-new-credential-" + "b".repeat(32);
    const fetcher = vi.fn().mockImplementation((path: string) => {
      if (path === "/api/radar/access") return Promise.resolve(status(true));
      writes += 1;
      return writes === 1 ? new Promise<Response>(resolve => { rejectOld = resolve; }) : Promise.resolve(new Response("{}"));
    });
    vi.stubGlobal("fetch", fetcher);
    await api.analystAccess(key);
    const old = api.saveAnnotation("bsc", "0x1", {}).catch(error => error);
    await api.analystAccess(newer);
    rejectOld(new Response(JSON.stringify({ detail: { code: "ANALYST_UNAUTHORIZED" } }), { status: 401 }));
    expect(await old).toMatchObject({ status: 401 });
    await api.saveAnnotation("bsc", "0x1", {});
    expect(new Headers(fetcher.mock.calls.at(-1)![1].headers).get("Authorization")).toBe(`Bearer ${newer}`);
  });
});

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
