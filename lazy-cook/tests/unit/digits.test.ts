import { describe, expect, it } from "vitest";
import { formatQuantity, parseNumber, toEnDigits, toFaDigits } from "@/lib/i18n/digits";
import { addDaysISO, diffDaysISO, formatDayMonth, todayISO } from "@/lib/i18n/jalali";

describe("digits", () => {
  it("converts to Persian digits", () => expect(toFaDigits("12 eggs 2026")).toBe("۱۲ eggs ۲۰۲۶"));
  it("normalises Persian and Arabic digits", () => expect(toEnDigits("۱۲٫۵ و ٣")).toBe("12.5 و 3"));
  it("parses either digit set", () => {
    expect(parseNumber("۱۷۵")).toBe(175);
    expect(parseNumber("72.5")).toBe(72.5);
    expect(parseNumber("۱٬۲۰۰")).toBe(1200);
    expect(parseNumber("abc")).toBeNull();
    expect(parseNumber("")).toBeNull();
  });
  it("formats fractions", () => {
    expect(formatQuantity(0.5)).toBe("½");
    expect(formatQuantity(1.25)).toBe("۱¼");
    expect(formatQuantity(3)).toBe("۳");
  });
});

describe("jalali dates", () => {
  it("formats 2026-11-05 as 14 Aban", () => expect(formatDayMonth("2026-11-05")).toBe("۱۴ آبان"));
  it("adds days across month ends", () => expect(addDaysISO("2026-12-30", 3)).toBe("2027-01-02"));
  it("diffs days", () => expect(diffDaysISO("2026-11-12", "2026-11-05")).toBe(7));
  it("today in a time zone", () => expect(todayISO("Asia/Yerevan", new Date("2026-11-05T22:30:00Z"))).toBe("2026-11-06"));
});
