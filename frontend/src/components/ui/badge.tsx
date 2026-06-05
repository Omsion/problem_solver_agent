import { cn } from "../../lib/utils";

const variants = {
  default: "bg-gray-100 text-gray-700",
  info: "bg-blue-50 text-blue-700",
  success: "bg-green-50 text-green-700",
  warning: "bg-amber-50 text-amber-700",
  error: "bg-red-50 text-red-700",
} as const;

interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: keyof typeof variants;
}

export const Badge = ({ className, variant = "default", children, ...props }: BadgeProps) => (
  <span
    className={cn(
      "inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full",
      variants[variant],
      className,
    )}
    {...props}
  >
    {children}
  </span>
);
