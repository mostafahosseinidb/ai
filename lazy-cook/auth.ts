import NextAuth, { type NextAuthConfig } from "next-auth";
import Nodemailer from "next-auth/providers/nodemailer";
import Google from "next-auth/providers/google";
import { PrismaAdapter } from "@auth/prisma-adapter";
import { createTransport } from "nodemailer";
import { randomInt } from "node:crypto";
import { db } from "@/lib/db";

/**
 * Email one-time code + Google (spec §7.1). Auth.js's email provider is used with a 6-digit code
 * as the "token": the user types it on /signin and we hit the standard callback URL with it.
 * Without EMAIL_SERVER (development) the code is printed to the server console.
 */
const emailServer = process.env.EMAIL_SERVER || undefined;

const providers: NextAuthConfig["providers"] = [
  Nodemailer({
    server: emailServer ?? { jsonTransport: true },
    from: process.env.EMAIL_FROM ?? "no-reply@example.com",
    maxAge: 10 * 60,
    generateVerificationToken: async () => String(randomInt(0, 1_000_000)).padStart(6, "0"),
    sendVerificationRequest: async ({ identifier, token, provider }) => {
      if (!emailServer) {
        console.info(`[auth] one-time code for ${identifier}: ${token}`);
        return;
      }
      await createTransport(emailServer).sendMail({
        to: identifier,
        from: provider.from,
        subject: `کد ورود آشپز تنبل: ${token}`,
        text: `کد ورودت: ${token}\nتا ۱۰ دقیقه معتبره.`,
        html: `<div dir="rtl" style="font-family:Tahoma,sans-serif;font-size:16px">کد ورودت:<br><b style="font-size:28px;letter-spacing:4px">${token}</b><br>تا ۱۰ دقیقه معتبره.</div>`,
      });
    },
  }),
];

if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
  providers.push(
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    }),
  );
}

export const googleEnabled = Boolean(
  process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET,
);

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: PrismaAdapter(db),
  session: { strategy: "database", maxAge: 60 * 60 * 24 * 60 },
  secret: process.env.AUTH_SECRET ?? process.env.NEXTAUTH_SECRET,
  trustHost: true,
  providers,
  pages: { signIn: "/signin", verifyRequest: "/signin/code", error: "/signin" },
  callbacks: {
    session({ session, user }) {
      if (session.user) session.user.id = user.id;
      return session;
    },
  },
});
