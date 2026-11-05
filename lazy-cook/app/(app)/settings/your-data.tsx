"use client";

import { useState } from "react";
import { Download, Trash2 } from "lucide-react";
import { Button, Card, Field, inputClass } from "@/components/ui/primitives";

export function YourData({ sharedWithOthers }: { sharedWithOthers: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  async function remove() {
    setBusy(true);
    setError(false);
    const res = await fetch("/api/account", {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ confirm: text.trim() }),
    });
    setBusy(false);
    if (!res.ok) return setError(true);
    window.location.href = "/";
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="font-bold">اطلاعاتت</h2>
      <a
        href="/api/export"
        download
        className="text-primary inline-flex min-h-11 items-center gap-2"
      >
        <Download className="size-4" /> دانلود همه‌ی اطلاعات (JSON)
      </a>
      {!confirming ? (
        <Button
          variant="ghost"
          className="text-danger self-start"
          onClick={() => setConfirming(true)}
        >
          <Trash2 className="size-4" /> حذف کامل حساب
        </Button>
      ) : (
        <div className="bg-danger/10 flex flex-col gap-2 rounded-[12px] p-3">
          <p className="text-sm leading-6">
            حسابت برای همیشه پاک می‌شه و برنمی‌گرده.{" "}
            {sharedWithOthers
              ? "این خونه با بقیه‌ی اعضاش می‌مونه؛ فقط تو ازش حذف می‌شی."
              : "برنامه‌ها، موجودی، لیست خرید و دستورهای خودت هم پاک می‌شن."}
          </p>
          <Field label="برای تأیید بنویس: حذف">
            <input className={inputClass} value={text} onChange={(e) => setText(e.target.value)} />
          </Field>
          {error && <p className="text-danger text-sm">حذف نشد. دوباره امتحان کن.</p>}
          <div className="flex gap-2">
            <Button variant="danger" disabled={busy || text.trim() !== "حذف"} onClick={remove}>
              برای همیشه حذف کن
            </Button>
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              بی‌خیال
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
