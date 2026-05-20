import { themes as prismThemes } from 'prism-react-renderer';
import type { Config } from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'SOS Docs',
  tagline: 'Local-first coordination kernel for heterogeneous AI agents',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://github.com',
  baseUrl: '/',

  organizationName: 'Mumega-com',
  projectName: 'sos',

  onBrokenLinks: 'warn',

  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },

  presets: [
    [
      'classic',
      {
        docs: {
          sidebarPath: './sidebars.ts',
          editUrl: 'https://github.com/Mumega-com/sos/tree/main/docs/',
        },
        blog: false,
        theme: {
          customCss: './src/css/custom.css',
        },
      } satisfies Preset.Options,
    ],
  ],

  themeConfig: {
    colorMode: {
      respectPrefersColorScheme: true,
    },
    navbar: {
      title: 'SOS',
      items: [
        {
          type: 'docSidebar',
          sidebarId: 'mainSidebar',
          position: 'left',
          label: 'Docs',
        },
        {
          type: 'docSidebar',
          sidebarId: 'apiSidebar',
          position: 'left',
          label: 'API',
        },
        // Research sidebar temporarily disabled — FRC papers need MDX fixes
        {
          href: 'https://github.com/Mumega-com/sos',
          label: 'GitHub',
          position: 'right',
        },
      ],
    },
    footer: {
      style: 'dark',
      links: [
        {
          title: 'Docs',
          items: [
            { label: 'Getting Started', to: '/docs/getting-started' },
            { label: 'Runtime Planes', to: '/docs/architecture/runtime-planes' },
            { label: 'API Reference', to: '/docs/api/squad-service' },
          ],
        },
        {
          title: 'Community',
          items: [
            { label: 'GitHub', href: 'https://github.com/Mumega-com/sos' },
          ],
        },
        {
          title: 'Related',
          items: [
            { label: 'Mirror', href: 'https://github.com/Mumega-com/mirror' },
            { label: 'Inkwell', href: 'https://github.com/Mumega-com/inkwell' },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Mumega Labs. MIT Licensed.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
