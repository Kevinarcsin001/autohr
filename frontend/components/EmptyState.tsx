import Link from "next/link";
import { FileSearch, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description: string;
  action?: {
    label: string;
    href?: string;
    onClick?: () => void;
  };
  size?: "sm" | "md" | "lg";
}

export function EmptyState({
  icon: Icon = FileSearch,
  title,
  description,
  action,
  size = "md",
}: EmptyStateProps) {
  const iconSizes = { sm: "h-10 w-10", md: "h-14 w-14", lg: "h-20 w-20" };
  const pSizes = { sm: "p-3", md: "p-4", lg: "p-5" };

  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div
        className={`mb-5 rounded-full bg-muted/50 ${pSizes[size]}`}
      >
        <Icon
          className={`${iconSizes[size]} text-muted-foreground/30`}
          strokeWidth={1.5}
          aria-hidden="true"
        />
      </div>
      <h3 className="text-lg font-semibold">{title}</h3>
      <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">
        {description}
      </p>
      {action && (
        <div className="mt-5">
          {action.href ? (
            <Button asChild>
              <Link href={action.href}>{action.label}</Link>
            </Button>
          ) : (
            <Button onClick={action.onClick}>{action.label}</Button>
          )}
        </div>
      )}
    </div>
  );
}
