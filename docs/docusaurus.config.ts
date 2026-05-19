import { themes as prismThemes } from 'prism-react-renderer';
import type { Config } from '@docusaurus/types';
import type * as Preset from '@docusaurus/preset-classic';

const config: Config = {
  title: 'Mumega Docs',
  tagline: 'AI Squad System — Squads, Tasks, Skills, Pipelines',
  favicon: 'img/favicon.ico',

  future: {
    v4: true,
  },

  url: 'https://docs.mumega.com',
  baseUrl: '/',

  organizationName: 'servathadi',
  projectName: 'mumega',

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
          editUrl: 'https://github.com/servathadi/mumega/tree/main/SOS/docs/',
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
      title: 'Mumega',
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
          href: 'https://github.com/servathadi/mumega',
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
            { label: 'Squad System', to: '/docs/squads' },
            { label: 'API Reference', to: '/docs/api/squad-service' },
          ],
        },
        {
          title: 'Community',
          items: [
            { label: 'Discord', href: 'https://discord.gg/mumega' },
            { label: 'GitHub', href: 'https://github.com/servathadi/mumega' },
          ],
        },
        {
          title: 'Products',
          items: [
            { label: 'DentalNearYou', href: 'https://dentalnearyou.ca' },
            { label: 'Grant & Funding', href: 'https://grantandfunding.com' },
            { label: 'The Realm of Patterns', href: 'https://therealmofpatterns.com' },
          ],
        },
      ],
      copyright: `Copyright © ${new Date().getFullYear()} Digid Inc. All Rights Reserved.`,
    },
    prism: {
      theme: prismThemes.github,
      darkTheme: prismThemes.dracula,
    },
  } satisfies Preset.ThemeConfig,
};

export default config;
