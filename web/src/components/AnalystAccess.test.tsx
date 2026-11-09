import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { AnalystWorkflow } from "./AnalystWorkflow";

const key = "synthetic-interface-credential-" + "a".repeat(32);
const replacement = "synthetic-interface-replacement-" + "b".repeat(32);
let host: HTMLDivElement, root: Root;
let enabled: boolean, accepted: string, unavailable: boolean, rejectWrites: boolean, writeCode: number;
let fetcher: ReturnType<typeof vi.fn>;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

beforeEach(() => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  api.lockAnalyst(); enabled = true; accepted = key; unavailable = false; rejectWrites = false; writeCode = 401;
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  fetcher = vi.fn().mockImplementation((path: string, options?: RequestInit) => {
    if (path === "/api/radar/access") {
      if (unavailable) return Promise.reject(new Error("offline"));
      return Promise.resolve(json({ enabled, authorized: enabled && new Headers(options?.headers).get("Authorization") === `Bearer ${accepted}` }));
    }
    if (options?.method === "PUT") return Promise.resolve(rejectWrites
      ? json({ detail: { code: writeCode === 503 ? "ANALYST_READ_ONLY" : "ANALYST_UNAUTHORIZED" } }, writeCode) : json({ revision: 2 }));
    return Promise.resolve(json({ revision: 1, note: "stored note" }));
  });
  vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { await act(() => root.unmount()); host.remove(); api.lockAnalyst(); vi.unstubAllGlobals(); });

const render = () => act(async () => root.render(<AnalystWorkflow chain="bsc" token="0xabc" />));
const button = (label: string) => [...host.querySelectorAll("button")].find(item => item.textContent === label)!;
const edit = async (element: HTMLInputElement | HTMLTextAreaElement, value: string) => act(async () => {
  const prototype = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
});
const unlock = async (value = key) => {
  await edit(host.querySelector<HTMLInputElement>('input[type="password"]')!, value);
  await act(async () => host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
};

it("starts read-only while displaying stored research and accepts a labelled unlock", async () => {
  await render();
  const note = host.querySelector("textarea")!;
  expect(note.value).toBe("stored note"); expect(note.readOnly).toBe(true);
  expect(button("保存备注").disabled).toBe(true); expect(button("创建提醒规则").disabled).toBe(true);
  const input = host.querySelector<HTMLInputElement>('input[type="password"]')!;
  expect(input.getAttribute("aria-label")).toBe("分析员访问密钥");
  expect(input.autocomplete).toBe("current-password");
  await unlock();
  expect(input.value).toBe(""); expect(note.readOnly).toBe(false);
  await edit(note, "working draft");
  await act(async () => button("保存备注").click());
  expect(host.textContent).toContain("备注已保存");
  const write = fetcher.mock.calls.find(([, options]) => options?.method === "PUT")!;
  expect(new Headers(write[1].headers).get("Authorization")).toBe(`Bearer ${key}`);
  await edit(note, "unsaved after lock");
  await act(async () => button("锁定研究编辑").click());
  expect(note.value).toBe("unsaved after lock"); expect(note.readOnly).toBe(true);
  expect(button("保存备注").disabled).toBe(true);
  expect(host.textContent).not.toContain(key);
  expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(key);
});

it("shows a disabled service without offering writes", async () => {
  enabled = false; await render();
  expect(host.textContent).toContain("只开放浏览");
  expect(button("保存备注").disabled).toBe(true); expect(button("创建提醒规则").disabled).toBe(true);
});

it("reports invalid authorization without losing stored notes", async () => {
  await render(); await unlock("incorrect");
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("密钥无效");
  expect(host.querySelector("textarea")?.value).toBe("stored note");
  expect(button("保存备注").disabled).toBe(true);
  expect(fetcher.mock.calls.some(([, options]) => options?.method === "PUT")).toBe(false);
});

it("keeps a failed authorization check unavailable and offers retry", async () => {
  unavailable = true; await render();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("授权状态读取失败");
  expect(button("保存备注").disabled).toBe(true);
  unavailable = false; await act(async () => button("重试授权状态").click());
  await unlock(); expect(host.querySelector("textarea")?.readOnly).toBe(false);
});

it("preserves a draft after server rotation and requires explicit reauthorization", async () => {
  await render(); await unlock();
  const note = host.querySelector("textarea")!; await edit(note, "preserved draft");
  rejectWrites = true; accepted = replacement;
  await act(async () => button("保存备注").click());
  expect(note.value).toBe("preserved draft"); expect(note.readOnly).toBe(true);
  expect(host.textContent).toContain("授权已失效");
  expect(button("保存备注").disabled).toBe(true);
  rejectWrites = false; await unlock(replacement);
  await act(async () => button("保存备注").click());
  expect(host.textContent).toContain("备注已保存");
});

it("refreshes a disabled write service without retrying the write or losing the draft", async () => {
  await render(); await unlock();
  const note = host.querySelector("textarea")!; await edit(note, "retained read-only draft");
  enabled = false; rejectWrites = true; writeCode = 503;
  await act(async () => button("保存备注").click());
  expect(host.textContent).toContain("只开放浏览");
  expect(note.value).toBe("retained read-only draft"); expect(note.readOnly).toBe(true);
  expect(host.querySelector('input[type="password"]')).toBeNull();
  expect(fetcher.mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(1);
});
