/** Parse a user-entered retention period without turning an empty field into zero. */
export function parseRetention(value: string, max: number): number | null {
  if (!/^[0-9]+$/.test(value)) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 1 && number <= max ? number : null;
}
