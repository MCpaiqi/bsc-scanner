export const bscscanTokenUrl = (address: string) => `https://bscscan.com/token/${encodeURIComponent(address)}`;
export const bscscanAddressUrl = (address: string) => `https://bscscan.com/address/${encodeURIComponent(address)}`;

export function safeExternalUrl(value?: string | null): string | null {
  if (!value || /[\u0000-\u001f]/.test(value)) return null;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}

export async function copyTextToClipboard(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch { /* Try the legacy path on insecure origins or denied permission. */ }
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const selection = document.getSelection();
  const ranges = selection ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i).cloneRange()) : [];
  const input = document.createElement("textarea");
  input.value = text;
  input.readOnly = true;
  input.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
  document.body.appendChild(input);
  try {
    input.focus();
    input.select();
    if (!document.execCommand?.("copy")) throw new Error("复制失败");
  } finally {
    input.remove();
    previous?.focus({ preventScroll: true });
    if (selection) { selection.removeAllRanges(); ranges.forEach((range) => selection.addRange(range)); }
  }
}
