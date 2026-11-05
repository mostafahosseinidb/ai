"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { z } from "zod";
import { signIn } from "@/auth";
import { safeNext } from "@/lib/safe-next";
import { allowCodeRequest } from "@/lib/server/rate-limit";

export async function sendCode(formData: FormData) {
  const next = safeNext(formData.get("next"));
  const email = z.string().trim().toLowerCase().email().safeParse(formData.get("email"));
  if (!email.success) redirect(`/signin?error=email&next=${encodeURIComponent(next)}`);
  const h = await headers();
  const ip = h.get("x-forwarded-for")?.split(",")[0]?.trim() || h.get("x-real-ip") || "unknown";
  if (!(await allowCodeRequest(email.data, ip)))
    redirect(`/signin?error=too_many&next=${encodeURIComponent(next)}`);
  await signIn("nodemailer", { email: email.data, redirect: false, redirectTo: next });
  redirect(`/signin/code?email=${encodeURIComponent(email.data)}&next=${encodeURIComponent(next)}`);
}

export async function googleSignIn(formData: FormData) {
  await signIn("google", { redirectTo: safeNext(formData.get("next")) });
}
