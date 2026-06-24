import starlight from '@astrojs/starlight';
import { defineConfig } from 'astro/config';

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
            { label: 'Plugin Manifests', slug: 'plugin-manifests' },
            { label: 'Runner Plugins', slug: 'runner-plugins' },
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
  ],
});
