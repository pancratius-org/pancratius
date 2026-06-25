# Verse Register v1

Runtime task: display-register inference for DOCX import.

This bundle supplies optional evidence for Russian book imports that contain
lineated source structure. The compiler still applies hard guards and rule
fallbacks; this scorer only scores candidates the policy allows.

Contract:

- task: `display_register`
- scorer family: `standardized_linear.v1`
- artifact schema: `pancratius.register_artifact.v1`
- observation schema: `pancratius.register_observation.v1`
- label space: `pancratius.display_register.labels.v1`
- feature set: `pancratius.verse_register_features.v1`
- language support: `ru`

Unsupported languages and missing bundles fall back to the rules-only register
policy. Runtime import does not download models or read research code.
