import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CopyAddress } from "./CopyAddress";

const token = "0x32e202f397523d07c79ad07556ce056d7b4758b6";
const pair = "0x21387258966f5d8a3acac79fc661611df810d926";
let host: HTMLDivElement; let root: Root;
beforeEach(() => { Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true }); host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(async () => { await act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("CopyAddress", () => {
  it.each([[token, "token"], [pair, "pair"]] as const)("copies the full %s despite truncated display and does not select a row", async (address, kind) => {
    const writeText = vi.fn().mockResolvedValue(undefined); const select = vi.fn();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    await act(() => root.render(<div onClick={select}><CopyAddress address={address} kind={kind} /></div>));
    expect(host.querySelector("code")?.textContent).toContain("...");
    expect(host.querySelector("code")?.textContent).not.toBe(address);
    await act(() => host.querySelector("button")!.click());
    expect(writeText).toHaveBeenCalledWith(address); expect(select).not.toHaveBeenCalled();
    expect(host.textContent).toContain("✓ 已复制");
    const link = host.querySelector("a")!;
    expect(link.href).toBe(`https://bscscan.com/${kind === "token" ? "token" : "address"}/${address}`);
    expect(link.target).toBe("_blank"); expect(link.rel).toBe("noreferrer");
    await act(() => link.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true })));
    expect(select).not.toHaveBeenCalled();
  });
  it("uses the insecure-origin fallback and restores focus", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    const exec = vi.fn(() => { expect((document.activeElement as HTMLTextAreaElement).value).toBe(pair); return true; });
    Object.defineProperty(document, "execCommand", { configurable: true, value: exec });
    await act(() => root.render(<CopyAddress address={pair} kind="pair" />));
    const button = host.querySelector("button")!; button.focus();
    await act(() => button.click());
    expect(exec).toHaveBeenCalledWith("copy"); expect(document.activeElement).toBe(button);
    expect(document.querySelector("textarea")).toBeNull();
  });
  it("reports failure and resets feedback after 1.8 seconds", async () => {
    vi.useFakeTimers();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: vi.fn().mockRejectedValue(new Error("denied")) } });
    Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn(() => false) });
    await act(() => root.render(<CopyAddress address={token} />));
    await act(() => host.querySelector("button")!.click());
    expect(host.textContent).toContain("复制失败");
    await act(() => vi.advanceTimersByTime(1800));
    expect(host.querySelector("button")?.textContent).toBe("复制");
    expect(host.querySelector("button")?.getAttribute("aria-label")).toBe("复制 Token 地址");
  });
});
