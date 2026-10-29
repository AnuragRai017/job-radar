export interface JobSummary {
  id: number;
  source: string;
  company: string;
  external_id: string;
  title: string;
  location: string;
  apply_url: string;
  posted_date: string | null;
  excerpt: string;
  is_active: boolean;
  scraped_at: string;
}

export interface JobDetail extends JobSummary {
  description_html: string;
  description_text: string;
  first_seen_at: string;
  last_seen_at: string;
}

export interface JobListResponse {
  items: JobSummary[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface CompanyStat {
  company: string;
  source: string;
  count: number;
  active_count: number;
}

export interface StatsResponse {
  total_jobs: number;
  active_jobs: number;
  companies: CompanyStat[];
  last_scrape_at: string | null;
  last_successful_scrape_at: string | null;
  last_run: { status: string; finished_at: string | null } | null;
}

interface CompanyMeta {
  short: string;
  color: string;
}

const COMPANY_META: Record<string, CompanyMeta> = {
  Deloitte: { short: "D", color: "var(--company-deloitte)" },
  Capgemini: { short: "C", color: "var(--company-capgemini)" },
  "JPMorgan Chase": { short: "J", color: "var(--company-jpmorgan)" },
  Google: { short: "G", color: "var(--company-google)" },
  Amazon: { short: "A", color: "var(--company-amazon)" },
  Microsoft: { short: "M", color: "var(--company-microsoft)" },
  HSBC: { short: "H", color: "var(--company-hsbc)" },
  EY: { short: "EY", color: "var(--company-ey)" },
  EXL: { short: "EXL", color: "var(--company-exl)" },
  Concentrix: { short: "CX", color: "var(--company-concentrix)" },
  Cadence: { short: "CA", color: "var(--company-cadence)" },
  Medallion: { short: "MD", color: "var(--company-medallion)" },
  "Deloitte India": { short: "DI", color: "var(--company-deloitte-india)" },
  "Capgemini India": { short: "CI", color: "var(--company-capgemini-india)" },
};

export function companyMeta(name: string): CompanyMeta {
  const known = COMPANY_META[name];
  if (known) return known;
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
  return { short: initials || "•", color: "var(--accent)" };
}

export function escapeHtml(value: string | null | undefined): string {
  return (value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export async function fetchJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) message = body.detail;
    } catch {
      // The response was not JSON; keep the status-based message.
    }
    throw new Error(message);
  }

  return (await response.json()) as T;
}

export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined).format(value ?? 0);
}

function toDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const normalized = /^\d{4}-\d{2}-\d{2}$/.test(value) ? `${value}T00:00:00Z` : value;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

const dayMs = 86_400_000;

export function formatPosted(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) return "Date not listed";

  const today = new Date();
  const startOfToday = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const startOfPosted = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const days = Math.round((startOfToday - startOfPosted) / dayMs);

  if (days <= 0) return "Posted today";
  if (days === 1) return "Posted yesterday";
  if (days < 7) return `Posted ${days} days ago`;

  return `Posted ${new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: date.getUTCFullYear() === today.getUTCFullYear() ? undefined : "numeric",
    timeZone: "UTC",
  }).format(date)}`;
}

export function formatDate(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function timeAgo(value: string | null | undefined): string {
  const date = toDate(value);
  if (!date) return "not yet";
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 14) return `${days}d ago`;
  return formatDate(value);
}

export function pageWindow(current: number, total: number, span = 2): Array<number | "gap"> {
  if (total <= 1) return [];
  const pages = new Set<number>([1, total]);
  for (let page = current - span; page <= current + span; page += 1) {
    if (page >= 1 && page <= total) pages.add(page);
  }
  const sorted = [...pages].sort((a, b) => a - b);
  const result: Array<number | "gap"> = [];
  let previous = 0;
  for (const page of sorted) {
    if (previous && page - previous > 1) result.push("gap");
    result.push(page);
    previous = page;
  }
  return result;
}

export function descriptionMarkup(html: string, text: string): string {
  if (html && html.trim()) return html;
  const safe = escapeHtml(text || "No description was provided for this posting.");
  return safe
    .split(/\n\s*\n/)
    .map((paragraph) => `<p>${paragraph.replaceAll("\n", "<br>")}</p>`)
    .join("");
}
