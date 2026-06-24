---
title: Docs Versions
description: How Lyra publishes Latest dev docs and archived release docs.
---

The published docs site is built from the `dev` branch. Until the first archived release exists, the site contains only the current development docs.

Lyra has no git tags yet, so the initial docs site does not include archived release versions. The `starlight-versions` package is installed for this site, but its plugin is not enabled because it requires at least one archived version.

When a release needs stable docs, archive the current docs under a version slug such as `0.1.0`, enable the plugin in `astro.config.mjs`, and add that slug to the `versions` list:

```js
starlightVersions({
  current: { label: 'Latest dev' },
  versions: [{ slug: '0.1.0', label: 'v0.1.0' }],
})
```

The intended model is:

- `Latest dev`: current docs from `docs/src/content/docs`.
- Release versions: copied documentation snapshots under version directories.

This keeps the everyday authoring path simple. The version selector appears as soon as the first real release archive is added.
