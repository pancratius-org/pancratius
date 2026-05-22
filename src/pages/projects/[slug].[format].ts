import type { GetStaticPaths } from "astro";

import { downloadStaticPaths, handleDownloadGET } from "@/lib/download-routes";

// Default-locale route: /projects/<slug>.<format>
export const getStaticPaths = (() => downloadStaticPaths("project", "ru")) satisfies GetStaticPaths;
export const GET = handleDownloadGET;
