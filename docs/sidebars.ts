import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  mainSidebar: [
    'getting-started',
    {
      type: 'category',
      label: 'Squads',
      items: ['squads/overview', 'squads/creating-squads', 'squads/tasks', 'squads/skills', 'squads/pipelines'],
    },
    {
      type: 'category',
      label: 'Guides',
      items: ['guides/onboard-project', 'guides/create-skill', 'guides/human-queue'],
    },
    {
      type: 'category',
      label: 'Architecture',
      items: ['architecture/overview', 'architecture/services', 'architecture/brain'],
    },
  ],
  apiSidebar: [
    {
      type: 'category',
      label: 'API Reference',
      items: ['api/squad-service', 'api/mirror-api', 'api/sos-mcp'],
    },
  ],
  // researchSidebar: FRC papers temporarily excluded from build — need MDX fixes
};

export default sidebars;
