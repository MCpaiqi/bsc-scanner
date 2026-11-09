// A bounded tab-local retry cache contains only public rule definitions and UUIDs.
const storageKey = "smms-alert-requests-v1";
const recent = new Map<string, string>();
const limit = 256;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const newId = () => {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

export function alertRequestId(signature: string): string {
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? "[]");
    if (Array.isArray(saved)) for (const item of saved.slice(-limit)) {
      if (Array.isArray(item) && typeof item[0] === "string" && item[0].length <= 4000 &&
          typeof item[1] === "string" && uuid.test(item[1]) && !recent.has(item[0])) recent.set(item[0], item[1]);
    }
  } catch { /* In-memory retry identities still survive closing/reopening details. */ }
  const id = recent.get(signature) ?? newId();
  recent.delete(signature); recent.set(signature, id);
  while (recent.size > limit) recent.delete(recent.keys().next().value!);
  try { sessionStorage.setItem(storageKey, JSON.stringify([...recent])); } catch { /* Storage can be disabled/full. */ }
  return id;
}
