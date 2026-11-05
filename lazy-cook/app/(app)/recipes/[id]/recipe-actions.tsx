"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Copy, Pencil, Star, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/primitives";
import { useToast } from "@/components/ui/toast";
import { buttonClass } from "@/components/ui/primitives";

export function RecipeActions({
  recipeId,
  own,
  inWishlist,
}: {
  recipeId: string;
  own: boolean;
  inWishlist: boolean;
}) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [wished, setWished] = useState(inWishlist);

  async function post(url: string, method = "POST", body?: unknown) {
    setBusy(true);
    try {
      const res = await fetch(url, {
        method,
        headers: body ? { "content-type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) throw new Error(String(res.status));
      return await res.json();
    } catch {
      toast({ text: "یه مشکلی پیش اومد." });
      return null;
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      <Button
        variant={wished ? "secondary" : "primary"}
        disabled={busy || wished}
        onClick={async () => {
          if (await post("/api/wishlist", "POST", { recipe_id: recipeId })) {
            setWished(true);
            toast({ text: "گذاشتمش برای برنامه‌ی بعدی." });
          }
        }}
      >
        <Star className="size-4" fill={wished ? "currentColor" : "none"} />{" "}
        {wished ? "تو لیست برنامه‌ی بعدیه" : "برای برنامه‌ی بعدی"}
      </Button>
      {own ? (
        <>
          <Link
            prefetch={false}
            href={`/recipes/${recipeId}/edit`}
            className={buttonClass("secondary")}
          >
            <Pencil className="size-4" /> ویرایش
          </Link>
          <Button
            variant="ghost"
            className="text-danger"
            disabled={busy}
            onClick={async () => {
              if (!confirm("این دستور حذف بشه؟ تو برنامه‌های قبلی می‌مونه.")) return;
              if (await post(`/api/recipes/${recipeId}`, "DELETE")) router.push("/recipes");
            }}
          >
            <Trash2 className="size-4" /> حذف
          </Button>
        </>
      ) : (
        <Button
          variant="secondary"
          disabled={busy}
          onClick={async () => {
            const res = await post(`/api/recipes/${recipeId}/copy`);
            if (res) router.push(`/recipes/${res.recipe.id}/edit`);
          }}
        >
          <Copy className="size-4" /> نسخه‌ی خودم رو بساز
        </Button>
      )}
    </div>
  );
}
