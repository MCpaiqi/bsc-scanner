import { useState } from "react";
import { safeExternalUrl } from "../utils/address";

export function TokenLogo({ symbol, address, imageUrl }: { symbol?: string | null; address: string; imageUrl?: string | null }) {
  const [failed, setFailed] = useState<string | null>(null);
  const url = safeExternalUrl(imageUrl);
  const hash = Array.from(address.toLowerCase()).reduce((value, char) => ((value * 31 + char.charCodeAt(0)) >>> 0), 0);
  return <span className="token-logo" style={{ background: `hsl(${hash % 360} 18% 21%)`, color: `hsl(${hash % 360} 38% 78%)` }}>
    {url && failed !== url ? <img src={url} alt={`${symbol || address} logo`} loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(url)} /> : <span aria-label="Token avatar fallback">{(symbol?.[0] || address.replace(/^0x/i, "")[0] || "?").toUpperCase()}</span>}
  </span>;
}
