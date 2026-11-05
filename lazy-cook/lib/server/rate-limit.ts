import "server-only";
import { db } from "@/lib/db";

/** Limits (spec doesn't fix numbers; see docs/DECISIONS.md). */
export const LIMITS = {
  sendPerEmailPerHour: 5,
  sendPerIpPerHour: 20,
  /** wrong codes per email before the code is thrown away */
  verifyFailures: 5,
  verifyWindowMinutes: 15,
};

async function count(kind: string, key: string, sinceMs: number) {
  return db.authAttempt.count({
    where: { kind, key, createdAt: { gte: new Date(Date.now() - sinceMs) } },
  });
}

/** Records the request; false when this email or IP asked for too many codes in the last hour. */
export async function allowCodeRequest(email: string, ip: string): Promise<boolean> {
  const hour = 3_600_000;
  const [byEmail, byIp] = await Promise.all([
    count("send", `email:${email}`, hour),
    count("send", `ip:${ip}`, hour),
  ]);
  if (byEmail >= LIMITS.sendPerEmailPerHour || byIp >= LIMITS.sendPerIpPerHour) return false;
  await db.authAttempt.createMany({
    data: [
      { kind: "send", key: `email:${email}` },
      { kind: "send", key: `ip:${ip}` },
    ],
  });
  // housekeeping: forget attempts older than a day
  if (Math.random() < 0.05)
    await db.authAttempt.deleteMany({
      where: { createdAt: { lt: new Date(Date.now() - 86_400_000) } },
    });
  return true;
}

export async function tooManyWrongCodes(email: string): Promise<boolean> {
  return (
    (await count("verify_fail", `email:${email}`, LIMITS.verifyWindowMinutes * 60_000)) >=
    LIMITS.verifyFailures
  );
}

/** A wrong code: count it, and once the limit is hit, invalidate every outstanding code for the email. */
export async function recordWrongCode(email: string) {
  await db.authAttempt.create({ data: { kind: "verify_fail", key: `email:${email}` } });
  if (await tooManyWrongCodes(email))
    await db.verificationToken.deleteMany({ where: { identifier: email } });
}
