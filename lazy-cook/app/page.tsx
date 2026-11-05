import { redirect } from "next/navigation";
import { auth } from "@/auth";
import { ButtonLink } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";

const POINTS = [
  ["📅", "برنامه‌ی روزانه که با تیک و لایک یاد می‌گیره چی دوست داری"],
  ["🧊", "موجودی یخچال و فریزر، با تاریخ انقضا — حتی «بعد از باز شدن»"],
  ["🛒", "لیست خرید خودکار، با اسم محلی مواد تو کشور خودت"],
  ["🍲", "باقی‌مونده‌ی ناهار یعنی شام آماده؛ کمتر آشپزی، کمتر ظرف"],
  ["🔥", "فر نداری؟ هیچ دستور فری بهت پیشنهاد نمی‌شه"],
];

export default async function Landing() {
  const session = await auth();
  if (session?.user) redirect("/today");
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col px-4 py-10">
      <div className="text-6xl" aria-hidden>
        🍳
      </div>
      <h1 className="mt-4 text-3xl leading-tight font-extrabold">{fa.appName}</h1>
      <p className="text-muted mt-3 text-lg leading-8">{fa.tagline}</p>
      <ul className="mt-8 flex flex-col gap-4">
        {POINTS.map(([e, t]) => (
          <li key={t} className="flex gap-3 leading-7">
            <span aria-hidden className="text-2xl">
              {e}
            </span>
            <span>{t}</span>
          </li>
        ))}
      </ul>
      <ButtonLink href="/signin" className="mt-10 min-h-12 text-lg">
        شروع کنیم
      </ButtonLink>
      <p className="text-muted mt-6 text-xs leading-6">{fa.healthDisclaimer}</p>
    </main>
  );
}
