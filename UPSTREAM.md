# Upstream provenance

This folder is a source-based adaptation of **PodFetch** by SamTV12345 and contributors.

- Repository: https://github.com/SamTV12345/PodFetch
- Source commit: `88ff1dfb96a7c8e85bdfab9f66f07c1a82d256bc`
- Imported: 2026-09-22, GitHub source tarball (mobile client and GitHub workflows omitted)
- License: Apache-2.0, retained verbatim in `LICENSE`
- Development backend: official `samuel19982/podfetch` image, pinned to
  `sha256:b4f0eac9d9d93f5b850e29546277ad3093fa6398e7a112c8a8867c0c9843883d`

The Rust source is retained for audit and future changes. The current local runtime
uses the pinned official image; it is not a locally compiled claim. UI modifications
and the companion service are developed here. See `CHANGES.md` for changed upstream
files. The product (Podsift) is not affiliated with Spotify.

The companion's read-only library adapter is carried forward from the operator's
private CLI work. No personal library, tokens or state are part of
the source export. Its provenance is distinct from the Apache-licensed PodFetch code.
