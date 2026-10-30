import { useState, type ReactNode } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useTranslation } from "./i18n/LanguageContext";
import LanguageToggle from "./LanguageToggle";

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13.5px] font-medium",
    "md:group-data-[collapsed=true]:justify-center md:group-data-[collapsed=true]:px-0",
    "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus",
    isActive ? "bg-accent-subtle text-accent" : "text-secondary hover:bg-hover hover:text-primary",
  ].join(" ");

// Secondary items get a lighter, smaller treatment — same rule as the
// primary class but without ever reaching full "text-primary" weight.
const secondaryNavLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[12.5px] font-medium",
    "md:group-data-[collapsed=true]:justify-center md:group-data-[collapsed=true]:px-0",
    "focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus",
    isActive ? "bg-accent-subtle text-accent" : "text-muted hover:bg-hover hover:text-secondary",
  ].join(" ");

const iconProps = {
  viewBox: "0 0 24 24",
  width: 16,
  height: 16,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

// Nav icons carry the whole meaning once the sidebar is collapsed, so every
// item needs one — no icon means an unreachable-by-sight route in the rail.
const icons: Record<string, ReactNode> = {
  dashboard: (
    <svg {...iconProps}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </svg>
  ),
  transactions: (
    <svg {...iconProps}>
      <path d="M4 7h16M4 12h16M4 17h10" />
      <path d="M17 15l2.5 2.5L22 15" />
    </svg>
  ),
  holdings: (
    <svg {...iconProps}>
      <path d="M21 12a9 9 0 1 1-9-9v9h9Z" />
    </svg>
  ),
  dividends: (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M15 9.5a3.8 3.8 0 0 0-5.6 1.2 4.7 4.7 0 0 0 0 2.6A3.8 3.8 0 0 0 15 14.5M8.5 11.2h4.8M8.5 13.1h4.8" />
    </svg>
  ),
  correlation: (
    <svg {...iconProps}>
      <circle cx="6" cy="7" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8.3 8.4 10.8 15.8M15.9 7.6l-2.6 8.2" />
    </svg>
  ),
  taxes: (
    <svg {...iconProps}>
      <path d="M4.5 20.5 19 6M7.5 4.5a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2ZM16.5 14.3a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2Z" />
    </svg>
  ),
};

const panelIcon = (
  <svg {...iconProps}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9.5 4v16" />
  </svg>
);

const STORAGE_KEY = "sidebarCollapsed";

export default function AppLayout() {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });

  const toggleSidebar = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      /* private mode: state still applies this session, just not persisted */
    }
  };

  // Dashboard/Holdings/Transactions are the daily views (where do I stand,
  // what do I hold, what did I spend) and get full nav weight. Everything
  // else — the analyses and retrospective reports — sits below a divider with
  // lighter visual weight; same routes, not nested.
  const primaryItems = [
    { to: "/", end: true, label: t.nav.dashboard, icon: icons.dashboard },
    { to: "/holdings", label: t.nav.holdings, icon: icons.holdings },
    { to: "/transactions", label: t.nav.transactions, icon: icons.transactions },
  ];
  const secondaryItems = [
    { to: "/correlation", label: t.nav.correlation, icon: icons.correlation },
    { to: "/dividends", label: t.nav.dividends, icon: icons.dividends },
    { to: "/taxes", label: t.nav.taxes, icon: icons.taxes },
  ];

  return (
    <div className="min-h-screen">
      {/* Module sidebar; collapses to an icon rail, and to a top bar below md
          where the rail state is ignored (nothing to reclaim horizontally).
          The top bar is two rows: brand + language, then the nav links on a
          strip that scrolls sideways. Six links don't fit a phone's width, and
          a row that overflows widens the whole document, which mobile
          browsers then zoom out to fit — shrinking the page into a column. */}
      <aside
        data-collapsed={collapsed}
        className={`group fixed inset-y-0 left-0 flex flex-col gap-6 overflow-y-auto border-r border-hairline-strong py-5 transition-[width] duration-200 ease-out max-md:static max-md:w-auto max-md:flex-row max-md:flex-wrap max-md:items-center max-md:gap-x-4 max-md:gap-y-2 max-md:overflow-visible max-md:border-r-0 max-md:border-b max-md:border-hairline-strong max-md:px-4 max-md:py-3 ${
          collapsed ? "w-14 px-2" : "w-60 px-3"
        }`}
      >
        <div className="flex items-center gap-1 max-md:contents">
          <Link
            to="/"
            className="truncate px-2.5 font-display text-[19px] font-semibold tracking-tight text-primary md:group-data-[collapsed=true]:hidden max-md:px-0"
          >
            {t.app.brand}
          </Link>

          <button
            type="button"
            onClick={toggleSidebar}
            aria-label={collapsed ? t.sidebar.expand : t.sidebar.collapse}
            title={collapsed ? t.sidebar.expand : t.sidebar.collapse}
            aria-expanded={!collapsed}
            className="ml-auto flex items-center justify-center rounded-md p-1.5 text-muted hover:bg-hover hover:text-primary focus-visible:outline-2 focus-visible:outline-focus md:group-data-[collapsed=true]:ml-0 md:group-data-[collapsed=true]:w-full max-md:hidden"
          >
            {panelIcon}
          </button>
        </div>

        <div className="contents max-md:order-last max-md:-mx-4 max-md:flex max-md:w-[calc(100%+2rem)] max-md:items-center max-md:gap-1 max-md:overflow-x-auto max-md:px-4 max-md:[scrollbar-width:none]">
          <nav className="flex flex-col gap-0.5 max-md:shrink-0 max-md:flex-row max-md:items-center max-md:gap-1">
            {primaryItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={navLinkClass}
                title={collapsed ? item.label : undefined}
              >
                <span className="shrink-0 max-md:hidden">{item.icon}</span>
                <span className="truncate md:group-data-[collapsed=true]:hidden">{item.label}</span>
              </NavLink>
            ))}
          </nav>

          <div className="h-px bg-hairline max-md:h-6 max-md:w-px max-md:shrink-0" />

          <nav className="flex flex-col gap-0.5 max-md:shrink-0 max-md:flex-row max-md:items-center max-md:gap-1">
            {secondaryItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={secondaryNavLinkClass}
                title={collapsed ? item.label : undefined}
              >
                <span className="shrink-0 max-md:hidden">{item.icon}</span>
                <span className="truncate md:group-data-[collapsed=true]:hidden">{item.label}</span>
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="mt-auto flex flex-col gap-1 max-md:ml-auto max-md:mt-0 max-md:flex-row">
          <LanguageToggle />
        </div>
      </aside>

      {/* The content always fills the window beside the sidebar, inside the
          page padding: a capped column left empty bands on wide screens. */}
      <main
        className={`px-10 py-8 transition-[margin] duration-200 ease-out max-md:ml-0 max-md:px-4 max-md:py-5 ${
          collapsed ? "ml-14" : "ml-60"
        }`}
      >
        {import.meta.env.MODE === "demo" && (
          <p className="mb-5 rounded-md bg-elevated px-3 py-2 text-[13px] text-secondary">
            {t.app.demoNotice}
          </p>
        )}

        <Outlet />

        {/* lightweight-charts is Apache-2.0 with an attribution requirement;
            the in-chart logo is off, so the notice lives here. */}
        <footer className="mt-10 text-[11px] text-muted">
          {t.app.chartsBy}{" "}
          <a
            href="https://www.tradingview.com/"
            target="_blank"
            rel="noreferrer noopener"
            className="underline underline-offset-2 hover:text-secondary"
          >
            TradingView
          </a>
        </footer>
      </main>
    </div>
  );
}
