import type { GetStaticPaths } from "astro";

import { bodyImageGET, workAssetImageStaticPaths } from "@/lib/body-images";

export const getStaticPaths = (() => workAssetImageStaticPaths()) satisfies GetStaticPaths;

export const GET = bodyImageGET;
