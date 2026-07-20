import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import { copyFile, readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import navigation from './navigation.json' with { type: 'json' };

const docsBase = (process.env.LYRA_DOCS_BASE ?? '/lyra/dev').replace(/\/$/, '');
const docsRef = process.env.LYRA_DOCS_REF ?? 'dev';

const sidebar = navigation.map((group) => ({
  label: group.label,
  items: group.items.map((item) => item === 'reference/generated'
    ? { autogenerate: { directory: item } }
    : item.replace(/\/index$/, '')),
}));

function ensureExpressiveCodeCssAsset() {
  return {
    name: 'ensure-expressive-code-css-asset',
    hooks: {
      'astro:build:done': async ({ dir, logger }) => {
        const distDir = fileURLToPath(dir);
        const astroDir = join(distDir, '_astro');
        let assetNames;

        try {
          assetNames = await readdir(astroDir);
        } catch {
          return;
        }

        const emittedCssAssets = assetNames.filter((name) => /^ec\.[\w-]+\.css$/.test(name));
        if (emittedCssAssets.length === 0) return;

        const htmlFiles = await findHtmlFiles(distDir);
        const referencedCssAssets = new Set();
        const ecCssReference = /\/_astro\/(ec\.[^"'<>\s]+\.css)/g;

        for (const file of htmlFiles) {
          const html = await readFile(file, 'utf8');
          for (const match of html.matchAll(ecCssReference)) {
            referencedCssAssets.add(match[1]);
          }
        }

        const existingAssets = new Set(assetNames);
        const sourceAsset = emittedCssAssets[0];

        for (const referencedAsset of referencedCssAssets) {
          if (existingAssets.has(referencedAsset)) continue;

          await copyFile(join(astroDir, sourceAsset), join(astroDir, referencedAsset));
          logger.info(`Copied ${sourceAsset} to ${referencedAsset}`);
        }
      },
    },
  };
}

async function findHtmlFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(entries.map(async (entry) => {
    const fullPath = join(directory, entry.name);
    if (entry.isDirectory()) return findHtmlFiles(fullPath);
    return entry.isFile() && entry.name.endsWith('.html') ? [fullPath] : [];
  }));

  return files.flat();
}

export default defineConfig({
  site: 'https://rodolfofigueroa.github.io',
  base: docsBase,
  integrations: [
    starlight({
      title: 'Lyra',
      expressiveCode: {
        styleOverrides: {
          windowFrameBoxShadow: 'none',
          windowTitleBarHeight: '0px',
        }
      },
      description: 'Schema-driven spatial metric jobs and plugin execution.',
      editLink: {
        baseUrl: `https://github.com/RodolfoFigueroa/lyra/edit/${docsRef}/docs/`,
      },
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/RodolfoFigueroa/lyra',
        },
      ],
      sidebar,
    }),
    ensureExpressiveCodeCssAsset(),
  ],
});
