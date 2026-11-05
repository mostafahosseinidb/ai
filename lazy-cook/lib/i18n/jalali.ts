import { format as formatJalali } from "date-fns-jalali";
import { format as formatGregorian } from "date-fns";
import { toFaDigits } from "./digits";

export type CalendarKind = "jalali" | "gregorian";

/** A calendar date as "YYYY-MM-DD" (no time zone). All plan dates use this form. */
export type ISODate = string;

/** Parse "YYYY-MM-DD" as a local-noon Date, which is safe from DST/time-zone edge cases. */
export function parseISODate(d: ISODate): Date {
  const [y, m, day] = d.split("-").map(Number);
  return new Date(y!, (m ?? 1) - 1, day ?? 1, 12, 0, 0);
}

export function toISODate(d: Date): ISODate {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Same as toISODate but reads the UTC parts (for @db.Date columns which come back as UTC midnight). */
export function dbDateToISO(d: Date): ISODate {
  return d.toISOString().slice(0, 10);
}

export function isoToDbDate(d: ISODate): Date {
  return new Date(`${d}T00:00:00.000Z`);
}

export function addDaysISO(d: ISODate, n: number): ISODate {
  const date = parseISODate(d);
  date.setDate(date.getDate() + n);
  return toISODate(date);
}

export function diffDaysISO(a: ISODate, b: ISODate): number {
  return Math.round((parseISODate(a).getTime() - parseISODate(b).getTime()) / 86_400_000);
}

/** Today's date in the given IANA time zone. */
export function todayISO(timeZone = "UTC", now = new Date()): ISODate {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
  return parts; // en-CA formats as YYYY-MM-DD
}

const WEEKDAYS_FA = ["یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه"];
const WEEKDAYS_SHORT_FA = ["ی", "د", "س", "چ", "پ", "ج", "ش"];

export function weekdayFa(d: ISODate): string {
  return WEEKDAYS_FA[parseISODate(d).getDay()]!;
}

export function weekdayShortFa(d: ISODate): string {
  return WEEKDAYS_SHORT_FA[parseISODate(d).getDay()]!;
}

/** Weekday names indexed by JS getDay() (0 = Sunday). */
export const WEEKDAY_NAMES_FA = WEEKDAYS_FA;

/** "۱۴ آبان" (jalali) or "۵ نوامبر" (gregorian) */
export function formatDayMonth(d: ISODate, cal: CalendarKind = "jalali"): string {
  const date = parseISODate(d);
  if (cal === "jalali") return toFaDigits(formatJalali(date, "d MMMM"));
  return toFaDigits(`${date.getDate()} ${GREGORIAN_MONTHS_FA[date.getMonth()]}`);
}

/** Full date: "پنجشنبه ۱۴ آبان ۱۴۰۵" */
export function formatFullDate(d: ISODate, cal: CalendarKind = "jalali"): string {
  const date = parseISODate(d);
  if (cal === "jalali") return toFaDigits(`${weekdayFa(d)} ${formatJalali(date, "d MMMM yyyy")}`);
  return toFaDigits(
    `${weekdayFa(d)} ${date.getDate()} ${GREGORIAN_MONTHS_FA[date.getMonth()]} ${date.getFullYear()}`,
  );
}

/** The small secondary date shown beside the primary one (the other calendar). */
export function formatSecondary(d: ISODate, primary: CalendarKind = "jalali"): string {
  const date = parseISODate(d);
  if (primary === "jalali") return formatGregorian(date, "d MMM");
  return toFaDigits(formatJalali(date, "d MMMM"));
}

export function jalaliDayNumber(d: ISODate): string {
  return toFaDigits(formatJalali(parseISODate(d), "d"));
}

const GREGORIAN_MONTHS_FA = [
  "ژانویه",
  "فوریه",
  "مارس",
  "آوریل",
  "مه",
  "ژوئن",
  "ژوئیه",
  "اوت",
  "سپتامبر",
  "اکتبر",
  "نوامبر",
  "دسامبر",
];
