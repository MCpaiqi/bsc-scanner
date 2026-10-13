const paths = {
  radar: "M12 3a9 9 0 1 0 9 9 M12 7a5 5 0 1 0 5 5 M12 12l8-8 M12 3v3 M3 12h3 M12 18v3 M18 12h3",
  alpha: "m3 16 5-5 4 3 8-10 M15 4h5v5 M3 21h18",
  wallet: "M20 8V5H5a2 2 0 0 0 0 4h16v11H5a2 2 0 0 1-2-2V7 M21 12h-6v5h6 M17 14.5h.01",
  health: "M3 12h4l3-8 4 16 3-8h4",
  refresh: "M20 8a8 8 0 0 0-14-3L3 8 M3 3v5h5 M4 16a8 8 0 0 0 14 3l3-3 M16 16h5v5",
  chevron: "m9 5 7 7-7 7",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  layers: "m12 3 10 5-10 5L2 8l10-5 M2 12l10 5 10-5 M2 16l10 5 10-5",
  shield: "m12 3 8 3v5c0 5-8 10-8 10S4 16 4 11V6l8-3 M12 8v5 M12 16h.01",
  arrow: "M5 12h14 m-5-5 5 5-5 5",
  filter: "M4 7h16 M7 12h10 M10 17h4",
  globe: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0 M3 12h18 M12 3c5 5 5 13 0 18-5-5-5-13 0-18",
} as const;

export function WorkspaceIcon({ name, size = 18 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
