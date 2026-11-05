"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { Button, inputClass } from "@/components/ui/primitives";
import { Sheet } from "@/components/ui/sheet";
import { fa } from "@/lib/i18n/fa";
import type { ISODate } from "@/lib/i18n/jalali";
import { useApi } from "./use-api";

export function FreeLog({
  date,
  logs,
}: {
  date: ISODate;
  logs: Array<{ id: string; text: string }>;
}) {
  const { call, busy } = useApi();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  return (
    <div className="flex flex-col gap-2">
      {logs.length > 0 && (
        <ul className="text-muted flex flex-col gap-1 text-sm">
          {logs.map((l) => (
            <li key={l.id}>➕ {l.text}</li>
          ))}
        </ul>
      )}
      <Button
        variant="ghost"
        className="text-primary self-start text-sm"
        onClick={() => setOpen(true)}
      >
        <Plus className="size-4" /> {fa.meal.freeLog}
      </Button>
      <Sheet open={open} onClose={() => setOpen(false)} title={fa.meal.freeLog}>
        <form
          className="flex flex-col gap-3"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!text.trim()) return;
            await call("/api/free-logs", "POST", { date, text });
            setText("");
            setOpen(false);
          }}
        >
          <input
            className={inputClass}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={fa.meal.freeLogPlaceholder}
            maxLength={300}
            required
          />
          <Button type="submit" disabled={busy}>
            {fa.meal.send}
          </Button>
        </form>
      </Sheet>
    </div>
  );
}
