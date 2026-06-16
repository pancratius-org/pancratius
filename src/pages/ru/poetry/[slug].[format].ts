import type { GetStaticPaths } from "astro";

import { downloadStaticPaths, handleDownloadGET } from "@/lib/download-routes";

// Default-locale route: /ru/poetry/<slug>.<format>
export const getStaticPaths = (() => downloadStaticPaths("poem", "ru")) satisfies GetStaticPaths;
export const GET = handleDownloadGET;
