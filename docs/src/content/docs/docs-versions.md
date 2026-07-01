---
title: Docs Versions
description: How Lyra publishes Latest dev docs and archived release docs.
---

The published docs site is built from the `dev` branch. Until the first archived release exists, the site contains only current development docs.

Release archives are added only when there is a real release to preserve. Avoid
creating a placeholder archive just to show a version selector.

When a release needs stable docs, archive the current docs under a version slug such as `0.1.0`, enable versioned docs in `astro.config.mjs`, and add that slug to the version list:

```js
starlightVersions({
  current: { label: 'Latest dev' },
  versions: [{ slug: '0.1.0', label: 'v0.1.0' }],
})
```

The intended model is:

- Current docs: authored in `docs/src/content/docs`.
- Release docs: copied documentation snapshots under version directories.

This keeps everyday authoring simple. The version selector appears after the first real release archive is configured.
