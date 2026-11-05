"use client";

import { useState } from "react";
import { MessageCircle } from "lucide-react";
import { Sheet } from "@/components/ui/sheet";
import { fa } from "@/lib/i18n/fa";

/** Floating assistant button, bottom-left above the tab bar. The chat itself arrives in phase 3. */
export function AssistantFab() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="bg-primary text-primary-contrast fixed bottom-20 left-4 z-40 flex min-h-12 items-center gap-2 rounded-full px-4 font-bold shadow-lg"
        aria-label={fa.assistant.fab}
      >
        <MessageCircle className="size-5" aria-hidden />
        <span className="text-sm">{fa.assistant.fab}</span>
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title={`💬 ${fa.assistant.fab}`}>
        <p className="text-muted leading-7">{fa.assistant.soon}</p>
      </Sheet>
    </>
  );
}
