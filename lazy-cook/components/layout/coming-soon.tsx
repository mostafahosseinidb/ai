import { EmptyState, PageHeader } from "@/components/ui/primitives";
import { fa } from "@/lib/i18n/fa";

export function ComingSoon({ title, emoji, text }: { title: string; emoji: string; text: string }) {
  return (
    <>
      <PageHeader title={title} />
      <EmptyState emoji={emoji} text={`${text} ${fa.common.comingSoon}`} />
    </>
  );
}
