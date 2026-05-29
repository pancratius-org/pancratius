import type { GetStaticPaths } from "astro";

import { downloadStaticPaths, handleDownloadGET } from "@/lib/download-routes";

export const getStaticPaths = (() => downloadStaticPaths("poem", "en")) satisfies GetStaticPaths;
export const GET = handleDownloadGET;
