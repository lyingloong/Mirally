// Retain the original rendering API while using Mirally's own mark in every theme.
export { currentThemeBranding } from "../app/theme-branding.ts";
export { mirallyMark as neutralMark } from "./mirally-mark.ts";
import { mirallyMarkSvg } from "./mirally-mark.ts";

export function neutralMarkSvg({ fill }: { fill: string; glyph: string }): string {
  return mirallyMarkSvg(fill);
}
