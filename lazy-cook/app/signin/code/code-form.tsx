"use client";

import { useState } from "react";
import { Button, Field, inputClass } from "@/components/ui/primitives";
import { toEnDigits } from "@/lib/i18n/digits";
import { fa } from "@/lib/i18n/fa";

export function CodeForm({ email, next }: { email: string; next: string }) {
  const [code, setCode] = useState("");
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        const token = toEnDigits(code).replace(/\D/g, "");
        const q = new URLSearchParams({ email, token, callbackUrl: next });
        window.location.href = `/api/auth/callback/nodemailer?${q}`;
      }}
    >
      <Field label={fa.auth.code}>
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          required
          dir="ltr"
          className={`${inputClass} text-center text-2xl tracking-[0.5em]`}
          maxLength={6}
          name="code"
        />
      </Field>
      <Button type="submit">{fa.auth.verify}</Button>
    </form>
  );
}
