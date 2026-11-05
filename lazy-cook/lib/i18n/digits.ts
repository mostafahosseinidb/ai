const FA = "۰۱۲۳۴۵۶۷۸۹";
const AR = "٠١٢٣٤٥٦٧٨٩";

/** Convert every ASCII digit in a string (or number) to a Persian digit. */
export function toFaDigits(input: string | number): string {
  return String(input).replace(/[0-9]/g, (d) => FA[Number(d)]!);
}

/** Normalise Persian / Arabic-Indic digits (and separators) to ASCII, for parsing user input. */
export function toEnDigits(input: string): string {
  return input
    .replace(/[۰-۹]/g, (d) => String(FA.indexOf(d)))
    .replace(/[٠-٩]/g, (d) => String(AR.indexOf(d)))
    .replace(/[٫]/g, ".")
    .replace(/[٬،]/g, ",");
}

/** Parse a number typed with either Persian or English digits. Returns null if not a number. */
export function parseNumber(input: string): number | null {
  const s = toEnDigits(input).trim().replace(/,/g, "");
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}

/** Format a number with Persian digits and the Persian decimal separator. */
export function formatNumber(n: number, maxFractionDigits = 1): string {
  return new Intl.NumberFormat("fa-IR", { maximumFractionDigits: maxFractionDigits }).format(n);
}

const FRACTIONS: Array<[number, string]> = [
  [0.25, "¼"],
  [0.5, "½"],
  [0.75, "¾"],
  [1 / 3, "⅓"],
  [2 / 3, "⅔"],
];

/** Format a quantity: whole part in Persian digits plus a vulgar fraction when it is a common one. */
export function formatQuantity(n: number): string {
  if (n <= 0) return toFaDigits(0);
  const whole = Math.floor(n + 1e-9);
  const frac = n - whole;
  if (frac < 0.01) return toFaDigits(whole);
  const match = FRACTIONS.find(([v]) => Math.abs(v - frac) < 0.02);
  if (match) return whole === 0 ? match[1] : `${toFaDigits(whole)}${match[1]}`;
  return formatNumber(n, 1);
}
