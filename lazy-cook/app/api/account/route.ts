import { NextResponse } from "next/server";
import { z } from "zod";
import { auth } from "@/auth";
import { deleteAccount } from "@/lib/server/account";

const body = z.object({ confirm: z.literal("حذف") });

export async function DELETE(req: Request) {
  const id = (await auth())?.user?.id;
  if (!id) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const parsed = body.safeParse(await req.json().catch(() => null));
  if (!parsed.success)
    return NextResponse.json({ error: "confirmation_required" }, { status: 400 });
  await deleteAccount(id);
  const res = NextResponse.json({ ok: true });
  for (const name of ["authjs.session-token", "__Secure-authjs.session-token"])
    res.cookies.delete(name);
  return res;
}
