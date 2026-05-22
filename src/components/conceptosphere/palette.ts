export const CONCEPTOSPHERE_PALETTE = [
  "#c25e4f", "#d8a24a", "#e9c66a", "#6fa48a", "#4d8aa6",
  "#7c6bb8", "#b06aa0", "#c8a37a", "#a8485a", "#6e8fb5",
  "#93a85a", "#c98a4a", "#5fa3a3", "#a48cc8", "#b9755a",
  "#d9876b", "#5d997f", "#b5934f", "#8a7fbf", "#a3625f",
] as const;

export function communityColor(id: number): string {
  return CONCEPTOSPHERE_PALETTE[id % CONCEPTOSPHERE_PALETTE.length];
}

export function communityRgb(id: number): [number, number, number] {
  const hex = communityColor(id).replace("#", "");
  return [
    parseInt(hex.slice(0, 2), 16),
    parseInt(hex.slice(2, 4), 16),
    parseInt(hex.slice(4, 6), 16),
  ];
}
