import { requireHousehold } from "@/lib/session";
import { getActivePlan } from "@/lib/server/plans";
import { loadPlanMeals } from "@/lib/server/day";
import { addDaysISO, dbDateToISO, formatDayMonth, todayISO, weekdayFa } from "@/lib/i18n/jalali";
import { ButtonLink, EmptyState, PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";
import { toFaDigits } from "@/lib/i18n/digits";
import { PlanSlot } from "./plan-slot";

export const metadata = { title: fa.plan.title };

export default async function PlanPage({
  searchParams,
}: {
  searchParams: Promise<{ d?: string }>;
}) {
  const { user, household } = await requireHousehold();
  const plan = await getActivePlan(household.id);
  const { d: focus } = await searchParams;
  const newButton = (
    <ButtonLink href="/plan/new" variant="secondary" className="text-sm">
      {fa.plan.newPlan}
    </ButtonLink>
  );
  if (!plan) {
    return (
      <>
        <PageHeader title={fa.plan.title} />
        <EmptyState
          emoji="🗓️"
          text={fa.today.empty}
          action={<ButtonLink href="/plan/new">{fa.today.makePlan}</ButtonLink>}
        />
      </>
    );
  }
  const start = dbDateToISO(plan.startDate);
  const today = todayISO(user.timezone);
  const meals = await loadPlanMeals(plan.id, household.id);
  const days = Array.from({ length: plan.days }, (_, i) => addDaysISO(start, i));
  const slots = household.activeMealSlots;
  const cooked = meals.filter(
    (m) =>
      m.recipe &&
      !m.leftoverOf &&
      (m.slot === "lunch" || m.slot === "dinner") &&
      m.status !== "replaced",
  );

  return (
    <>
      <PageHeader
        title={fa.plan.title}
        subtitle={`${toFaDigits(plan.days)} روز، از ${formatDayMonth(start, user.calendar)}`}
        action={newButton}
      />
      <div className="flex flex-col gap-4 px-4">
        {days.map((date) => (
          <section
            key={date}
            id={date}
            className={`bg-surface rounded-[16px] border p-3 ${date === (focus ?? today) ? "border-primary" : "border-border"}`}
          >
            <h2 className="mb-2 flex items-baseline justify-between px-1 font-bold">
              <a href={`/today?d=${date}`}>
                {weekdayFa(date)} {formatDayMonth(date, user.calendar)}
              </a>
              {date === today && <span className="text-primary text-xs">امروز</span>}
            </h2>
            <ul className="divide-border flex flex-col divide-y">
              {slots.map((slot) => {
                const meal = meals.find(
                  (m) => m.date === date && m.slot === slot && m.status !== "replaced",
                );
                const leftoverSources = cooked
                  .filter(
                    (c) =>
                      c.date < date || (c.date === date && slot === "dinner" && c.slot === "lunch"),
                  )
                  .filter((c) => c.date >= addDaysISO(date, -3));
                return (
                  <PlanSlot
                    key={slot}
                    planId={plan.id}
                    date={date}
                    slot={slot}
                    meal={meal ?? null}
                    leftoverSources={leftoverSources.map((c) => ({
                      id: c.id,
                      title: c.recipe!.titleFa,
                      date: c.date,
                      slot: c.slot,
                    }))}
                  />
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </>
  );
}
