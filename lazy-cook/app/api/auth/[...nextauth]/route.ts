import { NextResponse, type NextRequest } from "next/server";
import { handlers } from "@/auth";
import { recordWrongCode, tooManyWrongCodes } from "@/lib/server/rate-limit";

export const { POST } = handlers;

/**
 * Wrap the email-code callback: after 5 wrong codes in 15 minutes the code is thrown away and the
 * user has to request a new one (6 digits = 1,000,000 combinations; this keeps guessing hopeless).
 */
export async function GET(req: NextRequest) {
  const url = new URL(req.url);
  if (!url.pathname.endsWith("/callback/nodemailer")) return handlers.GET(req);

  const email = (url.searchParams.get("email") ?? "").trim().toLowerCase();
  if (email && (await tooManyWrongCodes(email))) {
    return NextResponse.redirect(new URL("/signin?error=too_many", url.origin));
  }
  const res = await handlers.GET(req);
  const location = res.headers.get("location") ?? "";
  if (email && /[?&]error=Verification/.test(location)) await recordWrongCode(email);
  return res;
}
