export function tokenDisplayName(symbol: string | null | undefined, name: string | null | undefined, address: string): string {
  return symbol?.trim() || name?.trim() || (address.length > 14 ? `${address.slice(0, 6)}…${address.slice(-4)}` : address);
}
