/** Countries offered in onboarding, with currency and the local languages used for ingredient names. */
export const COUNTRIES: Array<{
  code: string;
  nameFa: string;
  currency: string;
  languages: string[];
  timezone: string;
}> = [
  {
    code: "AM",
    nameFa: "ارمنستان",
    currency: "AMD",
    languages: ["hy", "ru"],
    timezone: "Asia/Yerevan",
  },
  { code: "TR", nameFa: "ترکیه", currency: "TRY", languages: ["tr"], timezone: "Europe/Istanbul" },
  { code: "DE", nameFa: "آلمان", currency: "EUR", languages: ["de"], timezone: "Europe/Berlin" },
  { code: "AT", nameFa: "اتریش", currency: "EUR", languages: ["de"], timezone: "Europe/Vienna" },
  { code: "NL", nameFa: "هلند", currency: "EUR", languages: ["nl"], timezone: "Europe/Amsterdam" },
  { code: "GB", nameFa: "انگلیس", currency: "GBP", languages: ["en"], timezone: "Europe/London" },
  {
    code: "CA",
    nameFa: "کانادا",
    currency: "CAD",
    languages: ["en", "fr"],
    timezone: "America/Toronto",
  },
  {
    code: "US",
    nameFa: "آمریکا",
    currency: "USD",
    languages: ["en"],
    timezone: "America/New_York",
  },
  {
    code: "AE",
    nameFa: "امارات",
    currency: "AED",
    languages: ["ar", "en"],
    timezone: "Asia/Dubai",
  },
  { code: "SE", nameFa: "سوئد", currency: "SEK", languages: ["sv"], timezone: "Europe/Stockholm" },
  { code: "FR", nameFa: "فرانسه", currency: "EUR", languages: ["fr"], timezone: "Europe/Paris" },
  { code: "IT", nameFa: "ایتالیا", currency: "EUR", languages: ["it"], timezone: "Europe/Rome" },
  {
    code: "AU",
    nameFa: "استرالیا",
    currency: "AUD",
    languages: ["en"],
    timezone: "Australia/Sydney",
  },
  { code: "IR", nameFa: "ایران", currency: "IRR", languages: ["fa"], timezone: "Asia/Tehran" },
];

export const LANGUAGE_NAME_FA: Record<string, string> = {
  hy: "ارمنی",
  ru: "روسی",
  tr: "ترکی",
  de: "آلمانی",
  nl: "هلندی",
  en: "انگلیسی",
  fr: "فرانسوی",
  ar: "عربی",
  sv: "سوئدی",
  it: "ایتالیایی",
  fa: "فارسی",
};
