import type { GetStaticPaths } from "astro";

import { downloadStaticPaths, handleDownloadGET } from "@/lib/download-routes";

// English-locale route: /en/projects/<slug>.<format>
export const getStaticPaths = (() => downloadStaticPaths("project", "en")) satisfies GetStaticPaths;
export const GET = handleDownloadGET;
