export function TerminalIcon({ name }: { name: "copy" | "external" | "chevron" | "search" }) {
  const paths = { copy: "M9 8h11v13H9z M15 4V2H3v14h2", external: "M14 3h7v7 M21 3L10 14 M10 3H3v18h18v-7", chevron: "m9 5 7 7-7 7", search: "m16 16 5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0" };
  return <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
