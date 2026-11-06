import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { AlphaCandidate } from "../types/radar";
import { alphaNumber, outcomeMultiplier } from "../utils/alpha";
import { AlphaDetail, AlphaView } from "./AlphaView";

const selected: AlphaCandidate = { identity: { chain: "bsc", token: "0xtoken", pair: "0xpair", symbol: null, name: null, image_url: null, quote_symbol: null, dex: null, dex_url: null }, pair_created_at: "", snapshot_at: "", observation_seconds: 30, current_age_seconds: 30, alpha_score: 12, score_version: "experimental-v2", status: "", risk_status: "UNKNOWN", components: {}, features: {}, positive_reasons: [], negative_reasons: [], warnings: [], outcome: {} };
const latest: AlphaCandidate = { ...selected, observation_seconds: 300, alpha_score: 42, features: { unique_buyers: 3 }, positive_reasons: ["latest reason"], warnings: ["risk data unavailable"], outcome: { price_basis: "RAW_PAIR_RATIO", current_return: "0.42", max_realisable_return: "1.31" } };
it("distinguishes verified coverage from a pending horizon", () => {
  const waiting = renderToStaticMarkup(<AlphaDetail selected={latest} detail={null} onClose={() => {}} />);
  expect(waiting).toContain("覆盖待验证");
  const covered = { ...latest, outcome: { ...latest.outcome, tracking_complete: true, future_coverage: { status: "VERIFIED" } } };
  const complete = renderToStaticMarkup(<AlphaDetail selected={covered} detail={null} onClose={() => {}} />);
  expect(complete).toContain("完整覆盖已验证");
});
let host: HTMLDivElement; let root: Root;
beforeEach(() => { Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.useRealTimers(); });

it("uses detail.latest for score, reasons, warnings, outcome and explicit UNKNOWN risk", () => {
  const html = renderToStaticMarkup(<AlphaDetail selected={selected} detail={{ latest, snapshots: [latest] }} onClose={() => {}} />);
  expect(html).toContain("42"); expect(html).toContain("latest reason"); expect(html).toContain("risk data unavailable");
  expect(html).toContain("1.42x"); expect(html).toContain("2.31x"); expect(html).toContain("未知"); expect(html).not.toContain("低"); expect(html).toContain("RAW_PAIR_RATIO");
});
it("uses a known name as the primary identity when the token symbol is blank", () => {
  const named = { ...selected, identity: { ...selected.identity, symbol: "  ", name: "Doge Artificial Intelligence" } };
  host.innerHTML = renderToStaticMarkup(<AlphaDetail selected={named} detail={null} onClose={() => {}} />);
  expect(host.querySelector(".identity-text strong")?.textContent).toBe(named.identity.name);
});
it("shows a readable address and explicit pending label when token metadata is whitespace", () => {
  const unknown = { ...selected, identity: { ...selected.identity, symbol: "  ", name: "  " } };
  host.innerHTML = renderToStaticMarkup(<AlphaDetail selected={unknown} detail={null} onClose={() => {}} />);
  expect(host.querySelector(".identity-text strong")?.textContent?.trim()).not.toBe("");
  expect(host.querySelector(".token-name")?.textContent).toBe("Metadata pending");
});
it.each([null, undefined, "", "UNKNOWN", "not-a-number", false])("unknown %s never becomes numeric zero", (value) => { expect(alphaNumber(value)).toBe("—"); expect(outcomeMultiplier(value)).toBe("—"); });
it("formats additive returns as multipliers and preserves a real zero", () => {
  expect(outcomeMultiplier(0)).toBe("1.00x"); expect(outcomeMultiplier(-0.5)).toBe("0.50x"); expect(outcomeMultiplier(7.94)).toBe("8.94x"); expect(alphaNumber(0)).toBe("0");
});
it("polls open detail every 15s and shows failures instead of stale success", async () => {
  vi.useFakeTimers();
  vi.spyOn(api, "alphaCandidates").mockResolvedValue({ candidates: [selected], mode: "", shadow_mode: true });
  vi.spyOn(api, "alphaStats").mockResolvedValue({ scored_tokens: 1, high_score_tokens: 0, matured_1h: 0, overall: {}, high_score: {} });
  const request = vi.spyOn(api, "alphaCandidate").mockResolvedValueOnce({ latest: selected, snapshots: [selected] }).mockResolvedValueOnce({ latest, snapshots: [selected, latest] }).mockRejectedValueOnce(new Error("offline"));
  await act(() => root.render(<AlphaView />));
  await act(() => host.querySelector<HTMLButtonElement>(".alpha-open")!.click());
  expect(request).toHaveBeenCalledWith("0xpair", expect.any(AbortSignal));
  await act(() => vi.advanceTimersByTime(15000));
  expect(host.querySelector(".alpha-detail")?.textContent).toContain("1.42x");
  await act(() => vi.advanceTimersByTime(15000));
  expect(host.textContent).toContain("详情加载失败：offline"); expect(host.querySelector(".alpha-detail")).toBeNull();
});

it("search matches symbol/name and row click opens a keyboard dismissible drawer", async () => {
  const known = { ...selected, identity: { ...selected.identity, symbol: "DOGEAI", name: "Doge AI" } };
  vi.spyOn(api, "alphaCandidates").mockResolvedValue({ candidates: [known], mode: "", shadow_mode: true });
  vi.spyOn(api, "alphaStats").mockResolvedValue({ scored_tokens: 1, high_score_tokens: 0, matured_1h: 0, overall: {}, high_score: {} });
  vi.spyOn(api, "alphaCandidate").mockResolvedValue({ latest: known, snapshots: [known] });
  await act(() => root.render(<AlphaView />));
  expect(host.textContent).toContain("DOGEAI"); expect(host.textContent).toContain("Doge AI");
  const input = host.querySelector("input")!;
  for (const term of ["dogeai", "doge ai", "0xtoken", "0xpair"]) {
    await act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, term); input.dispatchEvent(new Event("input", { bubbles: true })); });
    expect(host.querySelectorAll(".terminal-table tbody tr")).toHaveLength(1);
  }
  expect(host.querySelector(".candidate-card")).not.toBeNull();
  await act(() => host.querySelector<HTMLTableRowElement>(".terminal-table tbody tr")!.click());
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  expect(host.textContent).toContain("EARLY SIGNAL");
  await act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});
