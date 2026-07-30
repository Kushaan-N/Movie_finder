// Small shadcn-style primitives, hand-rolled so the app is self-contained.
import { cn } from "@/lib/utils";

export function Card({ className, ...props }) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card text-card-foreground shadow-sm backdrop-blur-sm",
        className
      )}
      {...props}
    />
  );
}

export function Button({ className, variant = "default", size = "default", ...props }) {
  const variants = {
    default: "bg-primary text-primary-foreground hover:bg-primary/90",
    outline: "border border-input bg-transparent hover:bg-muted",
    ghost: "hover:bg-muted",
    subtle: "bg-muted text-foreground hover:bg-muted/70",
    danger: "bg-red-500/90 text-white hover:bg-red-500",
  };
  const sizes = {
    default: "h-10 px-4 py-2 text-sm",
    sm: "h-8 px-3 text-xs",
    lg: "h-12 px-6 text-base",
    icon: "h-9 w-9",
  };
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    />
  );
}

export function Input({ className, ...props }) {
  return (
    <input
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background/60 px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
        className
      )}
      {...props}
    />
  );
}

export function Select({ className, children, ...props }) {
  return (
    <select
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background/60 px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className
      )}
      {...props}
    >
      {children}
    </select>
  );
}

export function Label({ className, children, hint, ...props }) {
  return (
    <label className={cn("mb-1.5 flex items-center gap-2 text-sm font-medium", className)} {...props}>
      {children}
      {hint ? <span className="text-xs font-normal text-muted-foreground">{hint}</span> : null}
    </label>
  );
}

export function Badge({ className, tone = "default", ...props }) {
  const tones = {
    default: "bg-muted text-muted-foreground",
    green: "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30",
    yellow: "bg-amber-500/15 text-amber-300 border border-amber-500/30",
    red: "bg-red-500/15 text-red-300 border border-red-500/30",
    blue: "bg-blue-500/15 text-blue-300 border border-blue-500/30",
    new: "bg-primary text-primary-foreground",
  };
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        tones[tone],
        className
      )}
      {...props}
    />
  );
}

export function Stepper({ value, onChange, min = 1, max = 99, id, label }) {
  const set = (v) => onChange(Math.max(min, Math.min(max, v)));
  return (
    <div className="inline-flex h-10 items-stretch overflow-hidden rounded-md border border-input">
      <button
        type="button"
        onClick={() => set(Number(value) - 1)}
        className="w-10 bg-muted/50 text-lg hover:bg-muted"
        aria-label={label ? `Decrease ${label}` : "decrease"}
      >
        −
      </button>
      <input
        id={id}
        type="number"
        aria-label={label}
        value={value}
        min={min}
        max={max}
        onChange={(e) => set(Number(e.target.value))}
        className="w-14 bg-background/60 text-center text-sm focus:outline-none [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none"
      />
      <button
        type="button"
        onClick={() => set(Number(value) + 1)}
        className="w-10 bg-muted/50 text-lg hover:bg-muted"
        aria-label={label ? `Increase ${label}` : "increase"}
      >
        +
      </button>
    </div>
  );
}

export function Switch({ checked, onChange, id, "aria-label": ariaLabel }) {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-label={ariaLabel}
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
        checked ? "bg-primary" : "bg-muted"
      )}
    >
      <span
        className={cn(
          "inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-5" : "translate-x-0.5"
        )}
      />
    </button>
  );
}
