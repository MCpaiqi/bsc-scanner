import { useEffect, useRef, useState } from "react";
import { bscscanAddressUrl, bscscanTokenUrl, copyTextToClipboard } from "../utils/address";
import { TerminalIcon } from "./TerminalIcon";

export function CopyAddress({ address, kind = "token", full = false, compact = false }: { address?: string | null; kind?: "token" | "pair"; full?: boolean; compact?: boolean }) {
  const [status, setStatus] = useState("复制");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => { setStatus("复制"); return () => clearTimeout(timer.current); }, [address]);
  if (!address) return <span>—</span>;
  const label = kind === "token" ? "Token" : "Pair";
  return <span className={`copy-address ${full ? "full-address" : ""} ${compact ? "compact-address" : ""}`}>
    <code title={address}>{full ? address : `${address.slice(0, 8)}...${address.slice(-5)}`}</code>
    <button type="button" title={`复制完整 ${label} 合约地址`} aria-label={`复制 ${label} 地址`} onClick={async (event) => {
      event.stopPropagation();
      clearTimeout(timer.current);
      try { await copyTextToClipboard(address); setStatus("✓ 已复制"); }
      catch { setStatus("复制失败"); }
      timer.current = setTimeout(() => setStatus("复制"), 1800);
    }}>{compact && status === "复制" ? <TerminalIcon name="copy" /> : null}<span className={compact && status === "复制" ? "sr-only" : ""} role="status" aria-live="polite">{status}</span></button>
    <a href={kind === "token" ? bscscanTokenUrl(address) : bscscanAddressUrl(address)} target="_blank" rel="noreferrer" title={`在 BscScan 查看 ${label}`} aria-label={`在 BscScan 查看 ${label}`} onClick={(event) => event.stopPropagation()}>{compact ? <TerminalIcon name="external" /> : "BscScan"}</a>
  </span>;
}
