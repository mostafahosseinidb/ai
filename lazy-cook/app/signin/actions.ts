"use server";

import { redirect } from "next/navigation";
import { z } from "zod";
import { signIn } from "@/auth";

export async function sendCode(formData: FormData) {
  const email = z.string().trim().toLowerCase().email().safeParse(formData.get("email"));
  if (!email.success) redirect("/signin?error=email");
  await signIn("nodemailer", { email: email.data, redirect: false, redirectTo: "/today" });
  redirect(`/signin/code?email=${encodeURIComponent(email.data)}`);
}

export async function googleSignIn() {
  await signIn("google", { redirectTo: "/today" });
}
