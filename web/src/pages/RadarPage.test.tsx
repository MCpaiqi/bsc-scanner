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

const clickButton = async (label: string) => {
  const button = [...host.querySelectorAll("button")].find(item => item.textContent === label)!;
  expect(button, label).toBeDefined();
  await act(async () => button.click());
};
it("uses global server counts and browses beyond the former 500 cap", async () => {
  vi.mocked(api.candidates).mockResolvedValue({ candidates: [candidate], count: 1, total: 505,
    tab_counts: { all: 505, high_signal: 90, watch: 410, high_risk: 5 }, universe_total: 600 });
  await act(async () => root.render(<RadarPage />));
  expect(host.querySelector('.tabs')?.textContent).toContain("链上发现505");
  expect(host.querySelector('.table-footer')?.textContent).toContain("505");
  await clickButton("下一页");
  expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('offset')).toBe('50');
  expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('limit')).toBe('50');
});
it("sends global searches, resets the page and discards obsolete responses", async () => {
  vi.useFakeTimers();
  vi.mocked(api.candidates).mockResolvedValue({ candidates: [candidate], count: 1, total: 505 });
  await act(async () => root.render(<RadarPage />));
  await clickButton("下一页");
  let resolveOld!: (value: Awaited<ReturnType<typeof api.candidates>>) => void;
  vi.mocked(api.candidates).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; }));
  await act(async () => vi.advanceTimersByTime(15000));
  const input = host.querySelector<HTMLInputElement>('.filters input')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'needle');
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await act(async () => vi.advanceTimersByTime(300));
  const params = vi.mocked(api.candidates).mock.calls.at(-1)![0];
  expect(params.get('q')).toBe('needle'); expect(params.get('offset')).toBe('0');
  await act(async () => resolveOld({ candidates: [{ ...candidate, name: 'obsolete page' }], count: 1, total: 505 }));
  expect(host.querySelector('tbody')?.textContent).not.toContain('obsolete page');
});
it("renders server order and distinguishes unknown amounts from zero", async () => {
  vi.mocked(api.candidates).mockResolvedValue({ candidates: [
    { ...candidate, name: 'Server first', priority_score: 1, features: { liquidity: 'UNKNOWN' } },
    { ...candidate, token_address: '0xsecond', name: 'Server second', priority_score: 99, features: { liquidity: 0 } },
  ], count: 2, total: 2 });
  await act(async () => root.render(<RadarPage />));
  const rows = host.querySelectorAll('tbody tr');
  expect(rows[0].textContent).toContain('Server first');
  expect(rows[1].textContent).toContain('$0');
});
it("recovers the last page when the live result set shrinks", async () => {
  vi.useFakeTimers();
  vi.mocked(api.candidates).mockResolvedValue({ candidates: [candidate], count: 1, total: 505 });
  await act(async () => root.render(<RadarPage />));
  await clickButton('下一页');
  vi.mocked(api.candidates).mockResolvedValueOnce({ candidates: [], count: 0, total: 1 });
  await act(async () => vi.advanceTimersByTime(15000));
  expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('offset')).toBe('0');
  expect(host.querySelector('tbody tr')?.textContent).toContain(candidate.name);
});
it("polls statistics and exposes stale data after failed refresh", async () => {
  vi.useFakeTimers();
  await act(async () => root.render(<RadarPage />));
  vi.mocked(api.livePerformance).mockRejectedValueOnce(new Error('offline'));
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api.livePerformance).toHaveBeenCalledTimes(2);
  expect(host.querySelector('.performance-panel')?.textContent).toContain('上次读取');
  expect(host.querySelector('.performance-panel')?.textContent).toContain('更新失败');
  expect(host.querySelector('.performance-metrics')?.textContent).not.toContain('0个');
});

it("never applies a late event's financial or risk payload over the canonical response", async () => {
  const authoritative = { ...candidate, features: { risk: "HIGH", liquidity: 100 } };
  vi.mocked(api.candidates).mockResolvedValue({ candidates: [authoritative], count: 1, total: 1 });
  await act(async () => root.render(<RadarPage />));
  const [onEvent] = vi.mocked(connectRadarEvents).mock.calls.at(-1)!;
  await act(async () => onEvent({ event_id: "late", event_type: "CANDIDATE_UPDATED", chain: "bsc", token: candidate.token_address,
    occurred_at: "2026-01-01T00:00:00Z", data: { features: { risk: "LOW" }, priority_score: 999 } }));
  expect(host.querySelector("tbody tr")?.textContent).toContain("高风险");
});

it("rejects a fetch started before reset and reports recovery until the new snapshot arrives", async () => {
  type Payload = Awaited<ReturnType<typeof api.candidates>>;
  let oldResolve!: (value: Payload) => void, freshResolve!: (value: Payload) => void;
  vi.mocked(api.candidates).mockReturnValueOnce(new Promise(resolve => { oldResolve = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { freshResolve = resolve; }));
  await act(async () => root.render(<RadarPage />));
  const reset = vi.mocked(connectRadarEvents).mock.calls.at(-1)![3]!;
  await act(async () => reset({ reason: "REPLAY_LIMIT", live_available: true }));
  await act(async () => oldResolve({ candidates: [{ ...candidate, name: "obsolete" }], count: 1, total: 1 }));
  expect(host.querySelector("tbody")?.textContent ?? "").not.toContain("obsolete");
  expect(host.querySelector(".activity-panel")?.textContent).toContain("重新同步");
  await act(async () => freshResolve({ candidates: [candidate], count: 1, total: 1 }));
  expect(host.querySelector("tbody tr")?.textContent).toContain(candidate.name);
  expect(host.querySelector(".activity-panel")?.textContent).toContain("快照已刷新");
});

it("keeps failed resnapshot explicit and lets the user retry", async () => {
  await act(async () => root.render(<RadarPage />));
  vi.mocked(api.candidates).mockRejectedValueOnce(new Error("offline"));
  const reset = vi.mocked(connectRadarEvents).mock.calls.at(-1)![3]!;
  await act(async () => reset({ reason: "SLOW_CONSUMER", live_available: true }));
  expect(host.querySelector(".activity-panel")?.textContent).toContain("数据未同步");
  const retry = [...host.querySelectorAll("button")].find(button => button.textContent === "重新刷新")!;
  await act(async () => retry.click());
  expect(host.querySelector(".activity-panel")?.textContent).toContain("快照已刷新");
});

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


it("sends all four sorts, direction, view and risk filters to the complete server query", async () => {
  await act(async () => root.render(<RadarPage />));
  const select = host.querySelector<HTMLSelectElement>('.sort-field select')!;
  for (const sort of ['priority', 'age', 'liquidity', 'volume']) {
    await act(async () => { select.value = sort; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('sort')).toBe(sort);
  }
  const direction = host.querySelector<HTMLSelectElement>('.candidate-filters select')!;
  await act(async () => { direction.value = 'asc'; direction.dispatchEvent(new Event('change', { bubbles: true })); });
  expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('direction')).toBe('asc');
  const highRisk = [...host.querySelectorAll('.tabs button')].find(item => item.textContent?.startsWith('高风险')) as HTMLButtonElement;
  await act(async () => highRisk.click());
  expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('view')).toBe('high_risk');
  expect(vi.mocked(api.candidates).mock.calls.at(-1)![0].get('offset')).toBe('0');
});

it("coalesces slow statistics reads, aborts on unmount and shows measured zero", async () => {
  vi.useFakeTimers();
  let release!: (value: Awaited<ReturnType<typeof api.livePerformance>>) => void;
  vi.mocked(api.livePerformance).mockReturnValueOnce(new Promise(resolve => { release = resolve; }));
  await act(async () => root.render(<RadarPage />));
  await act(async () => vi.advanceTimersByTime(30000));
  expect(api.livePerformance).toHaveBeenCalledOnce();
  await act(async () => release({ mode: 'FORWARD_OBSERVATION', data_status: 'CURRENT', horizons: { '5m': { HIGH_SIGNAL: {
    sample_count: 0, median_return: 0, win_rate: null, '2x_rate': null, rug_rate: null, max_drawdown: null,
  } } } }));
  expect(host.querySelector('.performance-metrics')?.textContent).toContain('0个');
  expect(host.querySelector('.performance-metrics')?.textContent).toContain('0.0%');
  const signal = vi.mocked(api.livePerformance).mock.calls.at(-1)![0]!;
  await act(async () => root.unmount());
  expect(signal.aborted).toBe(true);
  await act(async () => vi.advanceTimersByTime(30000));
  expect(api.livePerformance).toHaveBeenCalledOnce();
  root = createRoot(host);
});
