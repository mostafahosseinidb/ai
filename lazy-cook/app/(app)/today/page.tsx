import { requireHousehold } from "@/lib/session";
import { getActivePlan } from "@/lib/server/plans";
import { daySummaries, loadFreeLogs, loadPlanMeals, loadPrepTasks } from "@/lib/server/day";
import {
  addDaysISO,
  dbDateToISO,
  formatFullDate,
  formatSecondary,
  todayISO,
} from "@/lib/i18n/jalali";
import { DayStrip } from "@/components/meal/day-strip";
import { MealCard } from "@/components/meal/meal-card";
import { PrepTasks } from "@/components/meal/prep-tasks";
import { FreeLog } from "@/components/meal/free-log";
import { ButtonLink, EmptyState } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";

export const metadata = { title: fa.nav.today };

export default async function TodayPage({
  searchParams,
}: {
  searchParams: Promise<{ d?: string }>;
}) {
  const { user, household } = await requireHousehold();
  const today = todayISO(user.timezone);
  const { d } = await searchParams;
  const plan = await getActivePlan(household.id);

  if (!plan) {
    return (
      <>
        <Header date={today} calendar={user.calendar} />
        <EmptyState
          emoji="🗓️"
          text={fa.today.empty}
          action={<ButtonLink href="/plan/new">{fa.today.makePlan}</ButtonLink>}
        />
      </>
    );
  }

  const start = dbDateToISO(plan.startDate);
  const end = addDaysISO(start, plan.days - 1);
  const selected =
    d && /^\d{4}-\d{2}-\d{2}$/.test(d) && d >= start && d <= end
      ? d
      : today < start
        ? start
        : today > end
          ? end
          : today;
  const [meals, tasks, logs] = await Promise.all([
    loadPlanMeals(plan.id, household.id),
    loadPrepTasks(household.id, selected),
    loadFreeLogs(household.id, selected),
  ]);
  const days = daySummaries(start, plan.days, meals);
  const todays = meals.filter((m) => m.date === selected && m.status !== "replaced");
  const planDays = days.map((x) => x.date);
  const finished = today > end;

  return (
    <>
      <Header date={selected} calendar={user.calendar} />
      <DayStrip days={days} selected={selected} today={today} />
      <div className="flex flex-col gap-3 px-4 pt-2">
        {finished && (
          <div className="bg-success/10 rounded-[16px] p-4 leading-7">
            {fa.today.planFinished(Number(plan.days))}{" "}
            <ButtonLink href="/plan/new" className="mt-2 w-full">
              {fa.plan.newPlan}
            </ButtonLink>
          </div>
        )}
        {todays.length === 0 && (
          <EmptyState
            emoji="🌿"
            text={fa.today.noMealsToday}
            action={
              <ButtonLink variant="secondary" href={`/plan?d=${selected}`}>
                {fa.plan.add}
              </ButtonLink>
            }
          />
        )}
        {todays.map((m) => (
          <MealCard key={m.id} meal={m} planDays={planDays} slots={household.activeMealSlots} />
        ))}
        <FreeLog date={selected} logs={logs} />
        <PrepTasks tasks={tasks} />
      </div>
    </>
  );
}

function Header({ date, calendar }: { date: string; calendar: "jalali" | "gregorian" }) {
  return (
    <header className="px-4 pt-5 pb-3">
      <h1 className="text-2xl font-extrabold">{formatFullDate(date, calendar)}</h1>
      <p className="text-muted text-sm" dir="auto">
        {toFaDigits(formatSecondary(date, calendar))}
      </p>
    </header>
  );
}
