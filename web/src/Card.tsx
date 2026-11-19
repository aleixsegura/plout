import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface Props {
  title?: ReactNode;
  /** A small link in the header corner ("All holdings →"). */
  action?: { to: string; label: string };
  children: ReactNode;
  className?: string;
  /** Tighter padding for dense list cards. */
  dense?: boolean;
}

/** The dashboard/transactions card: shared header, padding and the `card`
 * section recipe (a single top hairline) from index.css. */
export default function Card({ title, action, children, className = "", dense = false }: Props) {
  return (
    <section className={`card flex min-w-0 flex-col ${className}`}>
      {(title || action) && (
        <header className="flex items-baseline justify-between gap-3 pt-4">
          {title && (
            <h2 className="text-[15px] font-semibold tracking-[-0.01em] text-primary">{title}</h2>
          )}
          {action && (
            <Link
              to={action.to}
              className="whitespace-nowrap text-[12.5px] font-medium text-secondary hover:text-primary hover:underline focus-visible:outline-2 focus-visible:outline-focus"
            >
              {action.label} →
            </Link>
          )}
        </header>
      )}
      <div className={`flex min-w-0 flex-1 flex-col ${dense ? "pb-4 pt-3" : "pb-5 pt-4"}`}>{children}</div>
    </section>
  );
}
