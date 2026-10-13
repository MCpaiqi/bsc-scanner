import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, connectRadarEvents } from "../api/client";
import type { Candidate } from "../types/radar";
import RadarPage from "./RadarPage";

vi.mock("../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/client")>(),
  connectRadarEvents: vi.fn(() => () => {}),
}));

const candidate = {
  chain: "bsc", token_address: "0x1234567890abcdef", pair_address: "0xabcdef1234567890",
  symbol: "DOGEAI", name: "Doge Artificial Intelligence", launchpad: "pancakeswap",
  priority_score: 10, features: {}, data_status: "OBSERVED", evidence: {},
  latest_signal_class: "NONE", latest_signal_score: 0, freshness: "FRESH", source_status: "OK",
} satisfies Candidate & { name: string };
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.mocked(connectRadarEvents).mockClear();
  vi.spyOn(api, "candidates").mockResolvedValue({ candidates: [candidate], count: 1, total: 1 });
  vi.spyOn(api, "health").mockResolvedValue({});
  vi.spyOn(api, "radarHealth").mockResolvedValue({});
  vi.spyOn(api, "streamStatus").mockResolvedValue({});
  vi.spyOn(api, "livePerformance").mockResolvedValue({ mode: "FORWARD_OBSERVATION", horizons: {} });
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("shows the scanned token's name beside its symbol", async () => {
  await act(async () => root.render(<RadarPage />));
  expect(host.querySelector("tbody tr")?.textContent).toContain(candidate.name);
  expect(host.querySelector("tbody tr")?.textContent).toContain(candidate.symbol);
});

it("searches scanned tokens by full name or pair address", async () => {
  await act(async () => root.render(<RadarPage />));
  const input = host.querySelector<HTMLInputElement>('.filters input')!;
  for (const term of [" artificial intelligence ", candidate.pair_address]) {
    await act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, term);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(host.querySelectorAll("tbody tr")).toHaveLength(1);
  }
});

it("uses the available name when the symbol is blank", async () => {
  vi.mocked(api.candidates).mockResolvedValue({ candidates: [{ ...candidate, symbol: "  " }], count: 1, total: 1 });
  await act(async () => root.render(<RadarPage />));
  expect(host.querySelector("tbody td strong")?.textContent).toBe(candidate.name);
});

it("distinguishes an open event stream from a chain still catching up", async () => {
  vi.useFakeTimers();
  vi.spyOn(api, "streamStatus").mockResolvedValue({
    status: "catching_up", worker_heartbeat_age_seconds: 2,
    runtime_worker_status: { status: "CATCHING_UP", live_ready: false, block_lag: 2100 },
  });
  await act(async () => root.render(<RadarPage />));
  const [, onError, onOpen] = vi.mocked(connectRadarEvents).mock.calls.at(-1)!;
  await act(async () => onOpen?.());
  expect(host.querySelector(".topbar")?.textContent).toContain("事件流已连接");
  expect(host.querySelector(".topbar")?.textContent).toContain("同步落后 2,100 区块");
  expect(host.querySelector(".topbar")?.textContent).not.toContain("链上已同步");
  await act(async () => onError?.());
  expect(host.querySelector(".topbar")?.textContent).toContain("事件流未连接");
  await act(async () => onOpen?.());
  expect(host.querySelector(".topbar")?.textContent).toContain("事件流已连接");
  vi.mocked(api.streamStatus).mockResolvedValue({ status: "live", worker_heartbeat_age_seconds: 1, runtime_worker_status: { status: "LIVE", live_ready: true, block_lag: 0 } });
  await act(async () => vi.advanceTimersByTime(15000));
  expect(host.querySelector(".topbar")?.textContent).toContain("链上已同步");
});

it("hydrates partial discovery events so new tokens appear in the live list with names", async () => {
  vi.useFakeTimers();
  vi.mocked(api.candidates).mockResolvedValueOnce({ candidates: [], count: 0, total: 0 });
  await act(async () => root.render(<RadarPage />));
  const [onEvent] = vi.mocked(connectRadarEvents).mock.calls.at(-1)!;
  await act(async () => {
    for (const event_id of ["new-1", "new-2"]) onEvent({
      event_id, event_type: "CANDIDATE_DISCOVERED", chain: "bsc", token: candidate.token_address,
      occurred_at: new Date().toISOString(), data: { token_address: candidate.token_address, chain: "bsc", priority_score: 10, features: {} },
    });
  });
  expect(host.querySelectorAll("tbody tr")).toHaveLength(0);
  await act(async () => vi.advanceTimersByTime(1000));
  expect(api.candidates).toHaveBeenCalledTimes(2);
  expect(host.querySelector("tbody tr")?.textContent).toContain(candidate.name);
  expect(host.querySelectorAll("tbody tr")).toHaveLength(1);
});

it.each(["DEGRADED", "FAILED", "DOWN", "ERROR", "STOPPED"])("shows explicit %s collection failures even when the old live-ready flag is true", async status => {
  vi.mocked(api.streamStatus).mockResolvedValue({ worker_heartbeat_age_seconds: 1, runtime_worker_status: { status, live_ready: true, block_lag: 2100 } });
  await act(async () => root.render(<RadarPage />));
  const header = host.querySelector(".topbar")!;
  expect(header.textContent).toContain("链上采集异常");
  expect(header.textContent).not.toContain("链同步待确认");
  expect(header.textContent).not.toContain("链上已同步");
  expect(header.querySelector('[title="同步落后 2,100 区块"]')).not.toBeNull();
});

it("marks an expired collection heartbeat instead of trusting the previous live-ready flag", async () => {
  vi.mocked(api.streamStatus).mockResolvedValue({ worker_heartbeat_age_seconds: 90, runtime_worker_status: { status: "LIVE", live_ready: true } });
  await act(async () => root.render(<RadarPage />));
  const header = host.querySelector(".topbar")!;
  expect(header.textContent).toContain("采集心跳过期");
  expect(header.textContent).not.toContain("链上已同步");
  expect(header.querySelector(".connection.connected")).toBeNull();
});

it("keeps an unknown collection state unconfirmed", async () => {
  vi.mocked(api.streamStatus).mockResolvedValue({});
  await act(async () => root.render(<RadarPage />));
  expect(host.querySelector(".topbar")?.textContent).toContain("链同步待确认");
  expect(host.querySelector(".topbar")?.textContent).not.toContain("链上采集异常");
});

it("polls health independently and does not overlap requests or keep timers after unmount", async () => {
  vi.useFakeTimers();
  let releaseHealth!: (value: { status: string }) => void;
  vi.mocked(api.health).mockImplementationOnce(() => new Promise(resolve => { releaseHealth = resolve; }));
  vi.mocked(api.radarHealth).mockRejectedValue(new Error("temporarily unavailable"));
  vi.mocked(api.streamStatus).mockResolvedValue({ status: "live", worker_heartbeat_age_seconds: 1, runtime_worker_status: { status: "LIVE", live_ready: true } });
  await act(async () => root.render(<RadarPage />));
  await act(async () => vi.advanceTimersByTime(30000));
  expect(api.health).toHaveBeenCalledTimes(1);
  expect(api.streamStatus).toHaveBeenCalledTimes(1);
  await act(async () => releaseHealth({ status: "HEALTHY" }));
  expect(host.querySelector(".topbar")?.textContent).toContain("链上已同步");
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api.health).toHaveBeenCalledTimes(2);
  const healthSignal = vi.mocked(api.health).mock.calls.at(-1)![0]!;
  const candidatesSignal = vi.mocked(api.candidates).mock.calls.at(-1)![1]!;
  const candidateCalls = vi.mocked(api.candidates).mock.calls.length;
  await act(() => root.unmount());
  expect(healthSignal.aborted).toBe(true);
  expect(candidatesSignal.aborted).toBe(true);
  await act(async () => vi.advanceTimersByTime(30000));
  expect(api.health).toHaveBeenCalledTimes(2);
  expect(api.candidates).toHaveBeenCalledTimes(candidateCalls);
  root = createRoot(host);
});
