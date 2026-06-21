import type { APIRoute } from "astro";

import { buildFeed } from "@/lib/feed";

// New videos, newest first.
export const GET: APIRoute = () => buildFeed("en", "videos");
