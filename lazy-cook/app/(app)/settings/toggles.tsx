"use client";

import { Chip } from "@/components/ui/primitives";
import { useApi } from "@/components/meal/use-api";
import { fa } from "@/lib/i18n/fa";

export function SettingsToggles({
  calendar,
  units,
}: {
  calendar: "jalali" | "gregorian";
  units: "metric" | "cups";
}) {
  const { call, busy } = useApi();
  const set = (patch: Record<string, string>) => call("/api/household", "PATCH", patch);
  return (
    <>
      <fieldset>
        <legend className="mb-2 text-sm font-medium">{fa.settings.calendar}</legend>
        <div className="flex gap-2">
          <Chip
            disabled={busy}
            active={calendar === "jalali"}
            onClick={() => set({ calendar: "jalali" })}
          >
            {fa.settings.jalali}
          </Chip>
          <Chip
            disabled={busy}
            active={calendar === "gregorian"}
            onClick={() => set({ calendar: "gregorian" })}
          >
            {fa.settings.gregorian}
          </Chip>
        </div>
      </fieldset>
      <fieldset>
        <legend className="mb-2 text-sm font-medium">{fa.settings.units}</legend>
        <div className="flex gap-2">
          <Chip
            disabled={busy}
            active={units === "metric"}
            onClick={() => set({ units: "metric" })}
          >
            {fa.settings.metric}
          </Chip>
          <Chip disabled={busy} active={units === "cups"} onClick={() => set({ units: "cups" })}>
            {fa.settings.cups}
          </Chip>
        </div>
      </fieldset>
    </>
  );
}
