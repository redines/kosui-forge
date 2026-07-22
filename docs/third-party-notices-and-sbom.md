# Third-party notices and SBOM plan

This document is an engineering plan, not legal advice or a completed legal review. Kosui Forge must not describe a public release as license-complete until a maintainer or qualified reviewer approves the exact artifacts and notice bundle.

## Current source dependencies

The Python distribution currently declares GitHubKit (including its selected schema packages), PySide6, and pyforgejo. Their resolved transitive dependencies must be inventoried from the built wheel and any future native desktop artifact rather than inferred only from a development environment. System Git, GitHub CLI, and OpenSSH are prerequisites and are not distributed by this project.

The imported [cross-platform architecture evidence](architecture/cross-platform-gui-plan.md#licensing-and-redistribution-review) records the source spike's observed licenses and the additional review required before bundling Qt in a native artifact or introducing Nuitka, keyring backends, or installer tooling. Those observations are planning inputs, not legal conclusions and do not make the interpreted desktop slice a release artifact.

## Required release evidence

Before publishing a distributable desktop artifact, the release lane must:

1. resolve and lock exact direct and transitive versions for the target platform;
2. generate an SPDX 2.3 or CycloneDX SBOM from the final artifact, including native libraries, Qt modules/plugins, generated schemas, and installer runtime components;
3. produce a `THIRD-PARTY-NOTICES` bundle containing the applicable license and copyright texts for components actually conveyed;
4. preserve source-offer, relinking, runtime-exception, and installation-information evidence where the selected licenses require it;
5. identify unknown or unclassified licenses and stop the release until each is resolved;
6. scan the final artifact for credentials, unexpected executables, missing native libraries, and components absent from the approved inventory;
7. archive tool versions, checksums, signatures, provenance/attestation, the SBOM, and the approved notice bundle together;
8. obtain maintainer/legal approval for the exact release contents.

## Verification policy

A lock file, `pip list`, build virtual environment, or source-tree license scan alone is not proof of shipped content. Each native operating-system build must inventory the signed/notarized artifact after packaging. Checksums and the SBOM must identify that final artifact, and the published notices must match that inventory.

No third-party software should be bundled merely for convenience. A proposal to bundle GitHub CLI, Git, OpenSSH, or another sidecar must first document provenance, update ownership, vulnerability response, redistribution obligations, measurable user benefit, and a narrower alternative analysis.
