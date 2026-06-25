# Verse Register v1

Runtime task: display-register inference for DOCX import.

This bundle supplies model-assisted display-register decisions for Russian
non-poem DOCX conversions that contain lineated source structure. The compiler
still applies hard guards; this scorer only scores candidates the policy allows.

Contract:

- task: `display_register`
- scorer family: `standardized_linear.v1`
- artifact schema: `pancratius.register_artifact.v1`
- observation schema: `pancratius.register_observation.v1`
- label space: `pancratius.display_register.labels.v1`
- feature set: `pancratius.verse_register_features.v1`
- language support: `ru`

The production rollout requires this committed bundle for Russian non-poem
conversions.
Unsupported rollout languages use an explicit rules fallback with diagnostics.
Missing or invalid required bundles are artifact contract failures. Runtime
import does not download models or read research code.
