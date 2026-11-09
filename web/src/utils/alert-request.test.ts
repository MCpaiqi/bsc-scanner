import { expect, it, vi } from "vitest";

it("keeps a retry identity after a page module reload and after revisiting earlier parameters", async () => {
  const first = await import("./alert-request");
  const signature = `reload-${Date.now()}`;
  const id = first.alertRequestId(signature);
  first.alertRequestId(signature + "-other-scope");
  expect(first.alertRequestId(signature)).toBe(id);
  vi.resetModules();
  const reloaded = await import("./alert-request");
  expect(reloaded.alertRequestId(signature)).toBe(id);
});
it("retains page-local retry identities when browser storage is disabled", async () => {
  const storage = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("disabled"); });
  const { alertRequestId } = await import("./alert-request");
  const signature = `disabled-${Date.now()}`;
  const id = alertRequestId(signature);
  expect(alertRequestId(signature)).toBe(id);
  expect(id).toMatch(/^[0-9a-f-]{36}$/);
  storage.mockRestore();
});
