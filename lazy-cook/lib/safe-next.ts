/** Only same-site paths are allowed as a post-sign-in destination (no open redirects). */
export function safeNext(next: unknown, fallback = "/today"): string {
  if (typeof next !== "string") return fallback;
  if (
    !next.startsWith("/") ||
    next.startsWith("//") ||
    next.startsWith("/\\") ||
    next.includes("://")
  )
    return fallback;
  return next.slice(0, 200);
}
