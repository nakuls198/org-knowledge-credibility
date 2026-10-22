// Shared UI primitives in "The Record" style: paper, ink, one highlighter.
// Structure follows shadcn/ui (cva variants, Radix for interactive parts); the look is
// square corners, ink rules instead of boxes and a yellow hover / focus.
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-[2px] text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4",
  {
    variants: {
      variant: {
        // primary action: solid ink, highlighter on hover
        default: "border border-ink bg-ink text-paper hover:bg-hl hover:text-ink active:bg-hl-soft",
        // everything else: outlined, highlighter on hover
        secondary: "border border-ink bg-transparent text-ink hover:bg-hl active:bg-hl-soft",
        ghost: "text-ink hover:bg-hl-soft",
        link: "px-0 text-ink underline decoration-[#e0c200] decoration-2 underline-offset-[3px] hover:bg-hl-soft",
      },
      size: { default: "h-9 px-4", sm: "h-8 px-3 text-xs", icon: "h-8 w-8" },
    },
    defaultVariants: { variant: "secondary", size: "default" },
  },
);

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, ...props }, ref) => {
  const Comp = asChild ? Slot : "button";
  return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
});
Button.displayName = "Button";

/** A panel is a rule, not a box: a 2px ink line across the top. */
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <section className={cn("border-t-2 border-ink pt-1", className)} {...props} />;
}

export function CardHeader({ title, description, action, className }: { title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 py-2.5", className)}>
      <div className="min-w-0">
        <h2 className="font-serif text-xl font-medium tracking-tight text-ink">{title}</h2>
        {description && <p className="mt-0.5 max-w-[80ch] text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("pb-2", className)} {...props} />;
}

/** Section title with the highlighter stroke above it. */
export function Section({ title, description, action, children, className }: { title: React.ReactNode; description?: React.ReactNode; action?: React.ReactNode; children?: React.ReactNode; className?: string }) {
  return (
    <section className={className}>
      <div className="sec">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          {action}
        </div>
      </div>
      {children}
    </section>
  );
}

const chipVariants = cva("inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-semibold whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-paper-2 text-ink",
      outline: "border border-line text-muted-foreground",
      ink: "bg-ink text-paper",
      hl: "bg-hl text-ink",
      soft: "bg-hl-soft text-ink",
      // legacy tone names map onto neutral chips; meaning is carried by a dot, not a fill
      supports: "bg-paper-2 text-ink",
      contradicts: "bg-paper-2 text-ink",
      ambiguous: "bg-hl-soft text-ink",
      accent: "bg-hl-soft text-ink",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ className, tone, dot, children, ...props }: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof chipVariants> & { dot?: string }) {
  return (
    <span className={cn(chipVariants({ tone }), className)} {...props}>
      {dot && <i aria-hidden="true" className="inline-block size-2 rounded-full" style={{ background: dot }} />}
      {children}
    </span>
  );
}

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn("h-9 w-full rounded-[2px] border-[1.5px] border-ink bg-card px-3 text-sm text-ink placeholder:text-muted-foreground focus:shadow-[0_0_0_3px_var(--hl)] focus:outline-none", className)} {...props} />
));
Input.displayName = "Input";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn("w-full rounded-[2px] border-[1.5px] border-ink bg-card px-3 py-2.5 font-serif text-lg leading-snug text-ink placeholder:text-muted-foreground focus:shadow-[0_0_0_3px_var(--hl)] focus:outline-none", className)} {...props} />
));
Textarea.displayName = "Textarea";

export function NativeSelect({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn("h-9 rounded-[2px] border-[1.5px] border-ink bg-card px-2.5 text-sm text-ink focus:shadow-[0_0_0_3px_var(--hl)] focus:outline-none", className)} {...props} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse bg-paper-2", className)} aria-hidden="true" />;
}

/** Newspaper tabs: plain words on a rule, the active one underlined in ink. */
export const Tabs = TabsPrimitive.Root;
export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return <TabsPrimitive.List className={cn("flex flex-wrap gap-x-6 border-b border-line", className)} {...props} />;
}
export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "-mb-px py-2.5 text-[0.92rem] font-medium text-muted-foreground hover:text-ink data-[state=active]:font-semibold data-[state=active]:text-ink data-[state=active]:shadow-[inset_0_-2px_0_var(--ink)]",
        className,
      )}
      {...props}
    />
  );
}
export const TabsContent = TabsPrimitive.Content;

/** Headline number: a rule on top, uppercase label, serif figure. */
export function Stat({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint?: React.ReactNode; tone?: "warn" }) {
  return (
    <div className="stat">
      <div className="kicker">{label}</div>
      <div className={cn("v", tone === "warn" && "hl inline-block")}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
    </div>
  );
}

export function EmptyState({ title, body, action }: { title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-1.5 border-t border-line py-6">
      <p className="font-serif text-lg text-ink">{title}</p>
      {body && <p className="max-w-[70ch] text-sm text-muted-foreground">{body}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="notice">
      <p className="font-semibold">Couldn&apos;t load this.</p>
      <p className="mt-0.5">{msg}</p>
      {onRetry && (
        <Button size="sm" className="mt-2" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

/** Page headline: serif, optional highlighted phrase, one-line standfirst. */
export function PageHeader({ title, description, actions, kicker }: { title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode; kicker?: string }) {
  return (
    <div className="hero mb-2 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        {kicker && <div className="kicker mt-6">{kicker}</div>}
        <h1 className={kicker ? "!mt-1.5" : undefined}>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="mb-3">{actions}</div>}
    </div>
  );
}
