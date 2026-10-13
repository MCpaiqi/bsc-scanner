export function knownNumber(value: unknown): number | null {
  if ((typeof value !== "number" && typeof value !== "string") || String(value).trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
export const alphaNumber = (value: unknown) => {
  const number = knownNumber(value);
  return number === null ? "—" : number.toLocaleString(undefined, { maximumFractionDigits: 2 });
};
export const outcomeMultiplier = (value: unknown) => {
  const number = knownNumber(value);
  return number === null ? "—" : `${(1 + number).toFixed(2)}x`;
};
export const alphaRisk = (status?: string) => ({ UNKNOWN: "未知", LOW: "低", MEDIUM: "中", HIGH: "高" }[status ?? "UNKNOWN"] ?? "未知");
