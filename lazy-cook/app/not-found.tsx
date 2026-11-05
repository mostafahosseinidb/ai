import { ButtonLink, EmptyState } from "@/components/ui/primitives";

export default function NotFound() {
  return (
    <EmptyState
      emoji="🍽️"
      text="این صفحه پیدا نشد."
      action={<ButtonLink href="/today">برگرد به امروز</ButtonLink>}
    />
  );
}
