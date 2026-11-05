"use client";

import { Button, EmptyState } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";

export default function Error({ reset }: { error: Error; reset: () => void }) {
  return (
    <EmptyState
      emoji="😕"
      text={fa.common.error}
      action={<Button onClick={reset}>{fa.common.retry}</Button>}
    />
  );
}
