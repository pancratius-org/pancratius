import type { GetStaticPaths } from "astro";

import { downloadStaticPaths, handleDownloadGET } from "@/lib/download-routes";

// English-locale route: /en/books/<slug>.<format>
export const getStaticPaths = (() => downloadStaticPaths("book", "en")) satisfies GetStaticPaths;
export const GET = handleDownloadGET;
