"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { fa } from "@/lib/i18n/fa";

/** Bottom sheet built on <dialog> (focus trap, Esc to close, backdrop for free). */
export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Closing the <dialog> because `open` turned false must not call onClose again: that would
  // clobber the state of whatever opened next (e.g. menu sheet → eaten-out sheet).
  const openRef = useRef(open);
  openRef.current = open;
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={() => {
        if (openRef.current) onClose();
      }}
      onClick={(e) => e.target === ref.current && onClose()}
      aria-label={title}
      className="bg-surface text-text m-0 mt-auto max-h-[85dvh] w-full max-w-none rounded-t-[16px] p-0 backdrop:bg-black/40 sm:mx-auto sm:max-w-md"
    >
      <div className="border-border bg-surface sticky top-0 flex items-center justify-between border-b px-4 py-2">
        <h2 className="font-bold">{title}</h2>
        <button
          onClick={onClose}
          aria-label={fa.common.close}
          className="hover:bg-surface-2 flex size-11 items-center justify-center rounded-[12px]"
        >
          <X className="size-5" />
        </button>
      </div>
      <div className="p-4 pb-8">{children}</div>
    </dialog>
  );
}
