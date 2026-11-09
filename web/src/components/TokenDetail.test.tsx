import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, ApiError } from "../api/client";
import { analystSession } from "../api/analyst-session";
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
  const version = analystSession.begin();
  analystSession.accept("synthetic-detail-credential-" + "a".repeat(32), version);
  vi.spyOn(api, "analystAccess").mockResolvedValue({ enabled: true, authorized: true });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.spyOn(api, "candidate").mockResolvedValue(candidate);
  vi.spyOn(api, "earlyBuyers").mockResolvedValue({ early_buyers: [] });
  vi.spyOn(api, "smartMoney").mockResolvedValue({ smart_money: null });
  vi.spyOn(api, "risk").mockResolvedValue({ risk: null });
  vi.spyOn(api, "annotation").mockResolvedValue({ revision: 1 });
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); api.lockAnalyst(); vi.restoreAllMocks(); });

it("polls research while preserving the focused draft and marks failed refresh stale", async () => {
  vi.useFakeTimers();
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  const note = host.querySelector('textarea')!; note.focus();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(note, 'draft');
    note.dispatchEvent(new Event('input', { bubbles: true }));
  });
  vi.mocked(api.candidate).mockRejectedValueOnce(new Error('offline'));
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api.candidate).toHaveBeenCalledTimes(2);
  expect(api.annotation).toHaveBeenCalledOnce();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain('更新失败');
  expect(host.querySelector('textarea')).toBe(note);
  expect(document.activeElement).toBe(note);
  expect(note.value).toBe('draft');
  await act(async () => vi.advanceTimersByTime(15000));
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect(note.value).toBe('draft');
  vi.useRealTimers();
});

it("refreshes research after a stream reset without reloading annotations or discarding the note draft", async () => {
  vi.mocked(api.annotation).mockResolvedValue({ revision: 1, note: "saved" });
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


const inputValue = async (element: HTMLInputElement | HTMLTextAreaElement, value: string) => {
  await act(async () => {
    const prototype = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value')!.set!.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
};
const buttonNamed = (name: string) => [...host.querySelectorAll('button')].find(button => button.textContent === name)!;
it("blocks annotation writes after a failed read and offers a visible retry", async () => {
  vi.mocked(api.annotation).mockRejectedValueOnce(new Error('offline'));
  const save = vi.spyOn(api, 'saveAnnotation');
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  expect(host.textContent).toContain('备注读取失败');
  expect(buttonNamed('保存备注').disabled).toBe(true);
  expect(save).not.toHaveBeenCalled();
  await act(async () => buttonNamed('重新读取备注').click());
  expect(buttonNamed('保存备注').disabled).toBe(true);
  await inputValue(host.querySelector('textarea')!, 'draft');
  expect(buttonNamed('保存备注').disabled).toBe(false);
});
it("saves the whole draft with its revision and leaves in-flight edits unsaved", async () => {
  let release!: (value: Record<string, unknown>) => void;
  const save = vi.spyOn(api, 'saveAnnotation').mockReturnValueOnce(new Promise(resolve => { release = resolve; }));
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await inputValue(host.querySelector('textarea')!, 'submitted');
  await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  expect(save).not.toHaveBeenCalled();
  await act(async () => buttonNamed('保存备注').click());
  expect(save.mock.calls[0][2]).toMatchObject({ note: 'submitted', favorite: true, expected_revision: 1 });
  expect(buttonNamed('保存中…').disabled).toBe(true);
  await inputValue(host.querySelector('textarea')!, 'new unsaved draft');
  await act(async () => release({ revision: 2 }));
  expect(host.querySelector('textarea')?.value).toBe('new unsaved draft');
  expect(host.textContent).toContain('当前修改尚未保存');
});
it("keeps manual drafts mounted when strict research recovery fails", async () => {
  await act(async () => root.render(<TokenDetail candidate={candidate} refreshKey={0} onClose={() => {}} />));
  const note = host.querySelector('textarea')!;
  await inputValue(note, 'keep me'); note.focus();
  vi.mocked(api.candidate).mockRejectedValueOnce(new Error('offline'));
  await act(async () => root.render(<TokenDetail candidate={candidate} refreshKey={1} onClose={() => {}} />));
  expect(host.querySelector('textarea')).toBe(note);
  expect(note.value).toBe('keep me');
  expect(document.activeElement).toBe(note);
});
it("creates a token-scoped alert by default and retries with the same request identity", async () => {
  const create = vi.spyOn(api, 'createAlertRule').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ id: 7, scope: 'TOKEN', token_address: candidate.token_address });
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await act(async () => buttonNamed('创建提醒规则').click());
  expect(create.mock.calls[0][0]).toMatchObject({ scope: 'TOKEN', token_address: candidate.token_address });
  expect(typeof create.mock.calls[0][0].request_id).toBe('string');
  expect(host.textContent).toContain('提醒创建失败');
  await act(async () => buttonNamed('创建提醒规则').click());
  expect(create.mock.calls[1][0].request_id).toBe(create.mock.calls[0][0].request_id);
  expect(host.textContent).toContain('已创建当前代币提醒 #7');
});
it("keeps a conflicted draft and reads the latest revision explicitly before retry", async () => {
  vi.mocked(api.annotation).mockResolvedValueOnce({ revision: 1, note: 'old' }).mockResolvedValueOnce({ revision: 2, note: 'other editor' });
  const save = vi.spyOn(api, 'saveAnnotation').mockRejectedValueOnce(new ApiError(409)).mockResolvedValueOnce({ revision: 3 });
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await inputValue(host.querySelector('textarea')!, 'my draft');
  await act(async () => buttonNamed('保存备注').click());
  expect(host.textContent).toContain('保存冲突');
  expect(host.querySelector('textarea')?.value).toBe('my draft');
  await act(async () => buttonNamed('读取最新版本并保留草稿').click());
  expect(host.textContent).toContain('other editor');
  expect(host.querySelector('textarea')?.value).toBe('my draft');
  await act(async () => buttonNamed('保存备注').click());
  expect(save.mock.calls[1][2]).toMatchObject({ expected_revision: 2, note: 'my draft' });
  expect(host.textContent).toContain('备注已保存');
});


it("creates a global rule only after explicitly selecting its scope and preserves zero", async () => {
  const create = vi.spyOn(api, 'createAlertRule').mockResolvedValue({ id: 9, scope: 'GLOBAL', token_address: null });
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  const scope = host.querySelector<HTMLSelectElement>('[aria-label="提醒作用范围"]')!;
  await act(async () => { scope.value = 'GLOBAL'; scope.dispatchEvent(new Event('change', { bubbles: true })); });
  await inputValue(host.querySelector<HTMLInputElement>('[aria-label="提醒评分"]')!, '0');
  await act(async () => buttonNamed('创建提醒规则').click());
  expect(create.mock.calls[0][0]).toMatchObject({ scope: 'GLOBAL', token_address: null,
    conditions: [{ field: 'smart_money_score', operator: '>=', value: 0 }] });
  expect(host.textContent).toContain('已创建全局 BSC提醒 #9');
});
it("does not apply a late save result to a newly selected token", async () => {
  let release!: (value: Record<string, unknown>) => void;
  const save = vi.spyOn(api, 'saveAnnotation').mockReturnValueOnce(new Promise(resolve => { release = resolve; }));
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await inputValue(host.querySelector('textarea')!, 'old subject');
  await act(async () => buttonNamed('保存备注').click());
  const signal = save.mock.calls[0][3]!;
  const next = { ...candidate, token_address: '0xnext', name: 'Next' };
  await act(async () => root.render(<TokenDetail candidate={next} onClose={() => {}} />));
  expect(signal.aborted).toBe(true);
  await inputValue(host.querySelector('textarea')!, 'new subject draft');
  await act(async () => release({ revision: 2 }));
  expect(host.querySelector('textarea')?.value).toBe('new subject draft');
  expect(host.textContent).not.toContain('备注已保存');
});
it("treats only 404 as an empty annotation and handles save failures without losing the draft", async () => {
  vi.mocked(api.annotation).mockRejectedValueOnce(new ApiError(404));
  const save = vi.spyOn(api, 'saveAnnotation').mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ revision: 1 });
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await inputValue(host.querySelector('textarea')!, 'first note');
  await act(async () => buttonNamed('保存备注').click());
  expect(save.mock.calls[0][2]).toMatchObject({ expected_revision: 0, note: 'first note' });
  expect(host.textContent).toContain('备注保存失败');
  expect(host.querySelector('textarea')?.value).toBe('first note');
  await act(async () => buttonNamed('保存备注').click());
  expect(host.textContent).toContain('备注已保存');
});
it("can create alerts without the optional randomUUID browser method", async () => {
  vi.spyOn(crypto, 'randomUUID').mockImplementation(() => { throw new Error('unavailable'); });
  const create = vi.spyOn(api, 'createAlertRule').mockResolvedValue({ id: 5, scope: 'TOKEN', token_address: candidate.token_address });
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await act(async () => buttonNamed('创建提醒规则').click());
  expect(create).toHaveBeenCalledOnce();
  expect(host.textContent).toContain('已创建当前代币提醒 #5');
});


it("preserves explicit conflict-recovery intent across a failed read even when the draft equals its old baseline", async () => {
  vi.mocked(api.annotation).mockResolvedValueOnce({ revision: 1, note: 'A' }).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce({ revision: 2, note: 'C' });
  let rejectSave!: (reason: unknown) => void;
  vi.spyOn(api, 'saveAnnotation').mockReturnValueOnce(new Promise((_resolve, reject) => { rejectSave = reject; }));
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await inputValue(host.querySelector('textarea')!, 'B');
  await act(async () => buttonNamed('保存备注').click());
  await inputValue(host.querySelector('textarea')!, 'A');
  await act(async () => rejectSave(new ApiError(409)));
  await act(async () => buttonNamed('读取最新版本并保留草稿').click());
  expect(host.textContent).toContain('备注读取失败');
  await act(async () => buttonNamed('重新读取备注').click());
  expect(host.querySelector('textarea')?.value).toBe('A');
  expect(host.textContent).toContain('C');
});
it("reuses an alert request identity after the workflow is closed and reopened", async () => {
  const create = vi.spyOn(api, 'createAlertRule').mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce({ id: 7, scope: 'TOKEN', token_address: candidate.token_address });
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await act(async () => buttonNamed('创建提醒规则').click());
  const original = create.mock.calls[0][0].request_id;
  await act(async () => root.render(null));
  await act(async () => root.render(<TokenDetail candidate={candidate} onClose={() => {}} />));
  await act(async () => buttonNamed('创建提醒规则').click());
  expect(create.mock.calls[1][0].request_id).toBe(original);
});


it("accepts a canonical alert response when the selected BSC contract uses checksum casing", async () => {
  const token = '0x' + 'A1'.repeat(20);
  const create = vi.spyOn(api, 'createAlertRule').mockResolvedValue({ id: 3, scope: 'TOKEN', token_address: token.toLowerCase() });
  await act(async () => root.render(<TokenDetail candidate={{ ...candidate, token_address: token }} onClose={() => {}} />));
  await act(async () => buttonNamed('创建提醒规则').click());
  expect(create.mock.calls[0][0].token_address).toBe(token.toLowerCase());
  expect(host.textContent).toContain('已创建当前代币提醒 #3');
});
