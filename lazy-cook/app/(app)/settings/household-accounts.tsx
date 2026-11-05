"use client";

import { useState } from "react";
import { Copy, Share2, UserMinus, UserPlus } from "lucide-react";
import { Button, Card } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { useApi } from "@/components/meal/use-api";

interface Account {
  userId: string;
  label: string;
  role: "owner" | "member";
  isMe: boolean;
}

export function HouseholdAccounts({
  accounts,
  iAmOwner,
}: {
  accounts: Account[];
  iAmOwner: boolean;
}) {
  const { call, busy } = useApi();
  const toast = useToast();
  const [link, setLink] = useState<string | null>(null);

  async function invite() {
    const res = await call<{ invite: { url: string } }>(
      "/api/household/invites",
      "POST",
      undefined,
      { refresh: false },
    );
    setLink(res.invite.url);
  }

  async function share() {
    if (!link) return;
    const text = `بیا تو «آشپز تنبل مهاجر» با هم برنامه‌ی غذا و لیست خرید داشته باشیم: ${link}`;
    if (navigator.share) {
      try {
        await navigator.share({ title: "آشپز تنبل مهاجر", text, url: link });
        return;
      } catch {
        /* cancelled — fall through to copy */
      }
    }
    try {
      await navigator.clipboard.writeText(link);
      toast({ text: "لینک کپی شد." });
    } catch {
      toast({ text: "کپی نشد؛ لینک رو دستی انتخاب کن." });
    }
  }

  return (
    <Card className="flex flex-col gap-3">
      <h2 className="font-bold">حساب‌های این خونه</h2>
      <ul className="flex flex-col gap-1">
        {accounts.map((a) => (
          <li key={a.userId} className="flex min-h-11 items-center justify-between gap-2">
            <span className="min-w-0 truncate" dir="auto">
              {a.label} {a.isMe && <span className="text-muted text-xs">(خودت)</span>}{" "}
              {a.role === "owner" && <span className="text-muted text-xs">· صاحب خونه</span>}
            </span>
            {a.role !== "owner" && (iAmOwner || a.isMe) && (
              <Button
                variant="ghost"
                disabled={busy}
                aria-label={a.isMe ? "ترک خانوار" : `حذف ${a.label}`}
                onClick={async () => {
                  if (!confirm(a.isMe ? "از این خونه بیرون بری؟" : `${a.label} از خونه حذف بشه؟`))
                    return;
                  await call(`/api/household/users/${a.userId}`, "DELETE");
                  if (a.isMe) window.location.href = "/onboarding";
                }}
              >
                <UserMinus className="size-4" />
              </Button>
            )}
          </li>
        ))}
      </ul>
      {link ? (
        <div className="flex flex-col gap-2">
          <p className="text-muted text-sm">
            این لینک رو بفرست؛ یه بار کار می‌کنه و تا ۷ روز معتبره.
          </p>
          <input
            readOnly
            value={link}
            dir="ltr"
            onFocus={(e) => e.currentTarget.select()}
            className="border-border bg-surface-2 min-h-11 rounded-[12px] border px-3 text-sm"
            aria-label="لینک دعوت"
          />
          <div className="flex gap-2">
            <Button onClick={share}>
              <Share2 className="size-4" /> بفرست
            </Button>
            <Button
              variant="secondary"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(link);
                  toast({ text: "لینک کپی شد." });
                } catch {
                  toast({ text: "کپی نشد؛ لینک رو دستی انتخاب کن." });
                }
              }}
            >
              <Copy className="size-4" /> کپی
            </Button>
          </div>
        </div>
      ) : (
        <Button variant="secondary" disabled={busy} onClick={invite}>
          <UserPlus className="size-4" /> دعوت یه نفر دیگه به این خونه
        </Button>
      )}
    </Card>
  );
}
