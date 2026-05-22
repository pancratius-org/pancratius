const BASE_URL = import.meta.env.BASE_URL || "/";

function normalizedBase(): string {
  if (!BASE_URL || BASE_URL === "/") return "";
  return `/${BASE_URL.replace(/^\/+|\/+$/g, "")}`;
}

export function sameSitePath(url: string): string {
  if (
    !url ||
    url.startsWith("#") ||
    url.startsWith("?") ||
    url.startsWith("//") ||
    /^[a-z][a-z0-9+.-]*:/i.test(url)
  ) {
    return url;
  }
  if (!url.startsWith("/")) return url;

  const base = normalizedBase();
  if (!base || url === base || url.startsWith(`${base}/`)) return url;
  return `${base}${url}`;
}

export function stripBasePath(pathname: string): string {
  const base = normalizedBase();
  if (!base) return pathname;
  if (pathname === base) return "/";
  return pathname.startsWith(`${base}/`) ? pathname.slice(base.length) : pathname;
}
