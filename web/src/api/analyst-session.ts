// Page-memory only. Generation checks fence late validation/write responses.
let credential: string | null = null;
let generation = 0;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach(listener => listener());

export const analystSession = {
  snapshot: () => credential !== null,
  credential: () => credential,
  generation: () => generation,
  subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  clear: (expectedGeneration?: number) => {
    if (expectedGeneration !== undefined && generation !== expectedGeneration) return;
    generation += 1; credential = null; notify();
  },
  begin: () => { generation += 1; credential = null; notify(); return generation; },
  accept: (value: string, expectedGeneration: number) => {
    if (generation !== expectedGeneration) return false;
    credential = value; notify(); return true;
  },
};
