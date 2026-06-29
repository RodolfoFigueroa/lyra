import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';
import { copyFile, readdir, readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

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
  base: '/lyra',
  integrations: [
    starlight({
      title: 'Lyra',
      expressiveCode: {
        styleOverrides: {
          windowFrameBoxShadow: 'none',
          windowTitleBarHeight: '0px',
        }
      },
      description: 'V2 plugin-runner and async job API documentation.',
      editLink: {
        baseUrl: 'https://github.com/RodolfoFigueroa/lyra/edit/dev/docs/',
      },
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/RodolfoFigueroa/lyra',
        },
      ],
      sidebar: [
        {
          label: 'Start Here',
          items: [
            { label: 'Overview', link: '/' },
            { label: 'Getting Started', slug: 'getting-started' },
            { label: 'Architecture', slug: 'architecture' },
          ],
        },
        {
          label: 'Develop Lyra',
          items: [
            { label: 'Contributor Guide', slug: 'contributor-guide' },
            { label: 'Local Development', slug: 'local-development' },
            { label: 'Testing And Quality', slug: 'testing-and-quality' },
          ],
        },
        {
          label: 'Build Plugins',
          items: [
            { label: 'Plugin Quickstart', slug: 'plugin-quickstart' },
            { label: 'Plugin Author Checklist', slug: 'plugin-author-checklist' },
            { label: 'Plugin Manifests', slug: 'plugin-manifests' },
            { label: 'Spatial Plugin Inputs', slug: 'spatial-plugin-inputs' },
            { label: 'Runner Plugins', slug: 'runner-plugins' },
          ],
        },
        {
          label: 'Python Packages',
          items: [
            { label: 'lyra-sdk', slug: 'lyra-sdk' },
            { label: 'lyra-api', slug: 'lyra-api' },
            { label: 'lyra-utils', slug: 'lyra-utils' },
          ],
        },
        {
          label: 'Use The API',
          items: [
            { label: 'Job API', slug: 'job-api' },
            { label: 'Metrics Catalog', slug: 'metrics-catalog' },
            { label: 'Python Client', slug: 'python-client' },
          ],
        },
        {
          label: 'Operations',
          items: [
            { label: 'Deployment', slug: 'deployment' },
            { label: 'Operations', slug: 'operations' },
            { label: 'Reference', slug: 'reference' },
            { label: 'Docs Versions', slug: 'docs-versions' },
          ],
        },
        {
          label: 'Agent Guide',
          items: [
            { label: 'AI Agent Guide', slug: 'ai-agent-guide' },
          ],
        },
      ],
    }),
    ensureExpressiveCodeCssAsset(),
  ],
});
