import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { Candidate } from "../types/radar";
import { TokenDetail } from "./TokenDetail";

const candidate = {
  chain: "bsc", token_address: "0x1234567890abcdef", symbol: "", name: "Doge Artificial Intelligence",
  launchpad: "pancakeswap", priority_score: 10, features: {}, data_status: "OBSERVED", evidence: {},
  latest_signal_class: "NONE", latest_signal_score: 0,
} satisfies Candidate & { name: string };
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.spyOn(api, "candidate").mockResolvedValue(candidate);
  vi.spyOn(api, "earlyBuyers").mockResolvedValue({ early_buyers: [] });
  vi.spyOn(api, "smartMoney").mockResolvedValue({ smart_money: null });
  vi.spyOn(api, "risk").mockResolvedValue({ risk: null });
  vi.spyOn(api, "annotation").mockResolvedValue({});
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); });

it("refreshes research after a stream reset without reloading annotations or discarding the note draft", async () => {
  vi.mocked(api.annotation).mockResolvedValue({ note: "saved" });
  await act(async () => root.render(<TokenDetail candidate={candidate} refreshKey={0} onClose={() => {}} />));
  const note = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(note, "unsaved draft");
    note.dispatchEvent(new Event("input", { bubbles: true }));
  });
  vi.mocked(api.candidate).mockResolvedValue({ ...candidate, name: "Refreshed research" });
  await act(async () => root.render(<TokenDetail candidate={candidate} refreshKey={1} onClose={() => {}} />));
  expect(api.candidate).toHaveBeenCalledTimes(2);
  expect(api.annotation).toHaveBeenCalledOnce();
  expect(host.querySelector("h2")?.textContent).toContain("Refreshed research");
  expect(host.querySelector("textarea")?.value).toBe("unsaved draft");
});

it("does not present an old research panel as refreshed when recovery fails", async () => {
  await act(async () => root.render(<TokenDetail candidate={candidate} refreshKey={0} onClose={() => {}} />));
  vi.mocked(api.candidate).mockRejectedValueOnce(new Error("offline"));
  await act(async () => root.render(<TokenDetail candidate={candidate} refreshKey={1} onClose={() => {}} />));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("详情数据未同步");
  expect(host.querySelector(".detail-content")).toBeNull();
  const retry = [...host.querySelectorAll("button")].find(button => button.textContent === "重新加载详情")!;
  await act(async () => retry.click());
  expect(host.querySelector(".detail-content")).not.toBeNull();
  expect(api.annotation).toHaveBeenCalledOnce();
});

it("uses the known token name in the detail heading and accessible label when symbol is missing", async () => {
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  expect(host.querySelector("h2")?.textContent).toContain(candidate.name);
  expect(host.querySelector('[role="dialog"]')?.getAttribute("aria-label")).toBe(`${candidate.name} 详情`);
});

it("shows the newly selected token while its detail loads and ignores late responses for an old token", async () => {
  const next = { ...candidate, token_address: "0xabcdef1234567890", name: "Next token" };
  let resolveOld!: (value: Candidate) => void;
  vi.mocked(api.candidate).mockReturnValueOnce(new Promise(resolve => { resolveOld = resolve; })).mockResolvedValueOnce(next);
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await act(async () => root.render(<TokenDetail candidate={next} onClose={() => {}} />));
  expect(host.querySelector("h2")?.textContent).toContain(next.name);
  await act(async () => resolveOld(candidate));
  expect(host.querySelector("h2")?.textContent).toContain(next.name);
  expect(host.querySelector("h2")?.textContent).not.toContain(candidate.name);
});

it("clears the previous token detail when a different token is opened", async () => {
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  const next = { ...candidate, token_address: "0xabcdef1234567890", name: "Next token" };
  vi.mocked(api.candidate).mockReturnValueOnce(new Promise(() => {}));
  await act(async () => root.render(<TokenDetail candidate={next} onClose={() => {}} />));
  expect(host.querySelector("h2")?.textContent).toContain(next.name);
  expect(host.querySelector("h2")?.textContent).not.toContain(candidate.name);
});
