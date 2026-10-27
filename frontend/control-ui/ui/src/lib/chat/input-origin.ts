/** A channel is shown only when the product recorded it on this user message. */
export function inputOriginLabel(message: unknown, surface: "desktop" | "mobile"): string | null {
  if (!message || typeof message !== "object") return null;
  const record = message as Record<string, unknown>;
  if (record.role !== "user") return null;
  if (record.input_origin === "weixin") return "微信";
  if (record.input_origin === "mobile" && surface === "desktop") return "手机网页";
  if (record.input_origin === "desktop" && surface === "mobile") return "电脑";
  return null;
}
