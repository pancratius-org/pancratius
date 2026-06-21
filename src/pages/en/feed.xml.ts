import type { APIRoute } from "astro";

import { buildFeed } from "@/lib/feed";

// Combined "latest" feed: new messages, videos, and poems, newest first.
export const GET: APIRoute = () => buildFeed("en", "all");
