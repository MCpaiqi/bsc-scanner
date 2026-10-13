import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { TokenLogo } from "./TokenLogo";
import { safeExternalUrl } from "../utils/address";

it("loads a safe logo lazily then falls back on image failure", async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const host = document.createElement("div"); const root = createRoot(host);
  await act(() => root.render(<TokenLogo address="0xabc" symbol="DOGE" imageUrl="https://example.com/logo.png" />));
  const img = host.querySelector("img")!;
  expect(img.getAttribute("loading")).toBe("lazy"); expect(img.getAttribute("referrerpolicy")).toBe("no-referrer");
  await act(() => img.dispatchEvent(new Event("error")));
  expect(host.querySelector("img")).toBeNull(); expect(host.textContent).toBe("D");
  const background = host.querySelector<HTMLElement>(".token-logo")!.style.background;
  await act(() => root.render(<TokenLogo address="0xabc" imageUrl={null} />));
  expect(host.textContent).toBe("A"); expect(host.querySelector<HTMLElement>(".token-logo")!.style.background).toBe(background);
  await act(() => root.unmount());
});
it("rejects unsafe image and external URLs", () => {
  for (const value of ["javascript:alert(1)", "data:image/png,a", "//example.com", "https://u:p@example.com"]) expect(safeExternalUrl(value)).toBeNull();
});
