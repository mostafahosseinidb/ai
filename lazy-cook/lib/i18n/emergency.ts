/** Local emergency numbers by ISO country code (spec §11.1). Default: 112 (works on most mobile networks). */
const NUMBERS: Record<string, string> = {
  AM: "911", TR: "112", DE: "112", AT: "112", NL: "112", GB: "999", US: "911", CA: "911",
  AE: "998", IR: "115", FR: "112", IT: "112", ES: "112", SE: "112", AU: "000",
};
export function emergencyNumber(countryCode?: string | null): string {
  return (countryCode && NUMBERS[countryCode.toUpperCase()]) || "112";
}
