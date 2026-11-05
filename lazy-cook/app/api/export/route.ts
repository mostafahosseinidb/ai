import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { exportData } from "@/lib/server/account";

export async function GET() {
  const id = (await auth())?.user?.id;
  if (!id) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const data = await exportData(id);
  const date = new Date().toISOString().slice(0, 10);
  return new NextResponse(JSON.stringify(data, null, 2), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "content-disposition": `attachment; filename="lazy-cook-export-${date}.json"`,
      "cache-control": "no-store",
    },
  });
}
