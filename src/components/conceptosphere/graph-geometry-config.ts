import type { ConceptosphereMode } from "./graph-types.ts";

interface CommunityLayoutGeometry {
  baseRadiusGraphUnits: number;
  radiusPerSqrtNodeGraphUnits: number;
  maxRadiusGraphUnits: number;
}

interface CommunityPlacementGeometry {
  ringTiersGraphUnits: readonly [number, ...number[]];
  ringGrowthGraphUnits: number;
  ringGrowthSizeDivisor: number;
}

interface NodeOverlapGeometry {
  defaultIterations: number;
  reducedMotionIterations: number;
  marginGraphUnits: number;
  distanceRatio: number;
}

interface CommunityOverlapGeometry {
  defaultIterations: number;
  reducedMotionIterations: number;
  marginGraphUnits: number;
  distanceRatio: number;
  strength: number;
}

interface HullGeometry {
  paddingViewportPx: number;
}

interface GraphGeometryProfile {
  communityPlacement: CommunityPlacementGeometry;
  communityLayout: CommunityLayoutGeometry;
  nodeOverlap: NodeOverlapGeometry;
  communityOverlap: CommunityOverlapGeometry | null;
  hull: HullGeometry;
}

export const GRAPH_GEOMETRY_PROFILE = {
  books: {
    communityPlacement: {
      ringTiersGraphUnits: [260, 380],
      ringGrowthGraphUnits: 12,
      ringGrowthSizeDivisor: 50,
    },
    communityLayout: {
      baseRadiusGraphUnits: 32,
      radiusPerSqrtNodeGraphUnits: 12.5,
      maxRadiusGraphUnits: 132,
    },
    nodeOverlap: {
      defaultIterations: 260,
      reducedMotionIterations: 120,
      marginGraphUnits: 9.5,
      distanceRatio: 1.22,
    },
    communityOverlap: {
      defaultIterations: 90,
      reducedMotionIterations: 45,
      marginGraphUnits: 22,
      distanceRatio: 0.90,
      strength: 0.32,
    },
    hull: {
      paddingViewportPx: 40,
    },
  },
  concepts: {
    communityPlacement: {
      ringTiersGraphUnits: [210, 330, 450],
      ringGrowthGraphUnits: 12,
      ringGrowthSizeDivisor: 50,
    },
    communityLayout: {
      baseRadiusGraphUnits: 6,
      radiusPerSqrtNodeGraphUnits: 3,
      maxRadiusGraphUnits: 28,
    },
    nodeOverlap: {
      defaultIterations: 150,
      reducedMotionIterations: 70,
      marginGraphUnits: 2,
      distanceRatio: 1.08,
    },
    communityOverlap: null,
    hull: {
      paddingViewportPx: 28,
    },
  },
} as const satisfies Record<ConceptosphereMode, GraphGeometryProfile>;
