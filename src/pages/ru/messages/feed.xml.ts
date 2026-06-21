import type { APIRoute } from "astro";

import { buildFeed } from "@/lib/feed";

// New messages, newest first.
export const GET: APIRoute = () => buildFeed("ru", "messages");
