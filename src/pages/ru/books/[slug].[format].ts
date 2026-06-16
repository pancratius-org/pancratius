import type { GetStaticPaths } from "astro";

import { downloadStaticPaths, handleDownloadGET } from "@/lib/download-routes";

// Default-locale route: /books/<slug>.<format>
export const getStaticPaths = (() => downloadStaticPaths("book", "ru")) satisfies GetStaticPaths;
export const GET = handleDownloadGET;
