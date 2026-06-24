import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://rodolfofigueroa.github.io',
  base: '/lyra',
  integrations: [
    starlight({
      title: 'Lyra',
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
            { label: 'Docs Versions', slug: 'docs-versions' },
          ],
        },
        {
          label: 'Using Lyra',
          items: [
            { label: 'Job API', slug: 'job-api' },
            { label: 'Metrics Catalog', slug: 'metrics-catalog' },
          ],
        },
        {
          label: 'Plugins',
          items: [
            { label: 'Plugin Manifests', slug: 'plugin-manifests' },
            { label: 'Runner Plugins', slug: 'runner-plugins' },
          ],
        },
        {
          label: 'Operations',
          items: [
            { label: 'Deployment', slug: 'deployment' },
            { label: 'Operations', slug: 'operations' },
            { label: 'Reference', slug: 'reference' },
          ],
        },
      ],
    }),
  ],
});
