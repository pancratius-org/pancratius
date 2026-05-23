// Root tooling config (not production source). PAN016-source-language must IGNORE
// it: it sits OUTSIDE the TS-mandated trees (src/scripts/tests), so the good
// fixture stays silent — this is the root-config false-positive regression.
module.exports = { plugins: {} };
