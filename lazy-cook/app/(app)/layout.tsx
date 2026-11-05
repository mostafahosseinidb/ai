import { requireHousehold } from "@/lib/session";
import { TabBar } from "@/components/layout/tab-bar";
import { AssistantFab } from "@/components/assistant/fab";
import { OfflineBanner } from "@/components/layout/offline-banner";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  await requireHousehold();
  return (
    <div className="mx-auto min-h-dvh max-w-md pb-36">
      <OfflineBanner />
      <main>{children}</main>
      <AssistantFab />
      <TabBar />
    </div>
  );
}
