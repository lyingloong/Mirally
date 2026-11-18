export {
  asNullableRecord as asProtocolRecord,
  isRecord as isProtocolRecord,
} from "@mirally/normalization-core/record-coerce";
export { normalizeOptionalString as normalizeOptionalProtocolString } from "@mirally/normalization-core/string-coerce";

/** Checks string presence without changing wire-significant whitespace. */
export function isNonEmptyProtocolString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}
