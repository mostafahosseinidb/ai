"use client";

import { useSyncExternalStore } from "react";
import { fa } from "@/lib/i18n/fa";

function subscribe(cb: () => void) {
  window.addEventListener("online", cb);
  window.addEventListener("offline", cb);
  return () => {
    window.removeEventListener("online", cb);
    window.removeEventListener("offline", cb);
  };
}

export function OfflineBanner() {
  const online = useSyncExternalStore(
    subscribe,
    () => navigator.onLine,
    () => true,
  );
  if (online) return null;
  return (
    <div role="status" className="bg-warning px-4 py-2 text-center text-sm text-white">
      {fa.common.offline}
    </div>
  );
}
