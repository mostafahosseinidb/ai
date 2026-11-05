"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState, useTransition } from "react";
import { useToast } from "@/components/ui/toast";
import { fa } from "@/lib/i18n/fa";

export interface Violation {
  code: string;
  detail: string;
}

export class RuleViolationError extends Error {
  constructor(public violations: Violation[]) {
    super("rule_violation");
  }
}

/** fetch JSON → refresh the server components. Rule violations come back as RuleViolationError. */
export function useApi() {
  const router = useRouter();
  const toast = useToast();
  const [pending, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);

  const call = useCallback(
    async <T = unknown>(
      url: string,
      method: string,
      body?: unknown,
      opts: { refresh?: boolean } = {},
    ): Promise<T> => {
      setBusy(true);
      try {
        const res = await fetch(url, {
          method,
          headers: body ? { "content-type": "application/json" } : undefined,
          body: body ? JSON.stringify(body) : undefined,
        });
        const json = await res.json().catch(() => ({}));
        if (res.status === 409 && json.error === "rule_violation")
          throw new RuleViolationError(json.details ?? []);
        if (!res.ok) {
          toast({ text: fa.common.error });
          throw new Error(json.error ?? res.statusText);
        }
        if (opts.refresh !== false) startTransition(() => router.refresh());
        return json as T;
      } finally {
        setBusy(false);
      }
    },
    [router, toast],
  );

  return { call, busy: busy || pending };
}
