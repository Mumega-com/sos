#!/usr/bin/env python3
"""
Create a new agent from template.

Usage:
    python create_agent.py my_agent_name
    python create_agent.py my_agent_name --description "Agent description"

This script:
1. Creates a new directory: agents/my_agent_name/
2. Copies template files (config.yml, agent.py, .env.example)
3. Creates symlinks to shared knowledge/ and channels/ modules
4. Initializes a knowledge_base/ folder
5. Provides next steps for customization
"""

import argparse
import shutil
import sys
from pathlib import Path


def create_agent(name: str, description: str = None):
    """Create a new agent from template.

    Args:
        name: Agent name (will be used as directory name)
        description: Optional agent description
    """
    # Validate name
    if not name.replace('_', '').replace('-', '').isalnum():
        print(f"❌ Error: Agent name must be alphanumeric (with _ or -)")
        return False

    # Set up paths
    agents_dir = Path(__file__).parent
    template_dir = agents_dir / 'template'
    new_agent_dir = agents_dir / name

    # Check if agent already exists
    if new_agent_dir.exists():
        print(f"❌ Error: Agent '{name}' already exists at {new_agent_dir}")
        return False

    # Check if template exists
    if not template_dir.exists():
        print(f"❌ Error: Template directory not found at {template_dir}")
        return False

    print(f"\n🤖 Creating new agent: {name}")
    print(f"📁 Location: {new_agent_dir}\n")

    try:
        # 1. Create agent directory
        new_agent_dir.mkdir(parents=True)
        print(f"✓ Created directory: {new_agent_dir}")

        # 2. Copy config.yml
        shutil.copy(template_dir / 'config.yml', new_agent_dir / 'config.yml')
        print(f"✓ Copied config.yml")

        # 3. Copy .env.example
        shutil.copy(template_dir / '.env.example', new_agent_dir / '.env.example')
        print(f"✓ Copied .env.example")

        # 4. Copy agent.py
        shutil.copy(template_dir / 'agent.py', new_agent_dir / 'agent.py')
        (new_agent_dir / 'agent.py').chmod(0o755)  # Make executable
        print(f"✓ Copied agent.py (executable)")

        # 5. Create symlinks to shared modules
        (new_agent_dir / 'knowledge').symlink_to(template_dir / 'knowledge', target_is_directory=True)
        print(f"✓ Created symlink: knowledge/ -> template/knowledge/")

        (new_agent_dir / 'channels').symlink_to(template_dir / 'channels', target_is_directory=True)
        print(f"✓ Created symlink: channels/ -> template/channels/")

        (new_agent_dir / 'tools').symlink_to(template_dir / 'tools', target_is_directory=True)
        print(f"✓ Created symlink: tools/ -> template/tools/")

        # 6. Create knowledge_base directory
        knowledge_base = new_agent_dir / 'knowledge_base'
        knowledge_base.mkdir()

        # Add a sample file
        sample_file = knowledge_base / 'README.md'
        sample_file.write_text(f"""# {name} Knowledge Base

Add your knowledge files here! Supported formats:
- `.txt` - Plain text files
- `.md` - Markdown files
- `.py` - Python code files
- `.json` - JSON data files
- `.yml` / `.yaml` - YAML configuration files

The agent will automatically load all files from this directory.

## Example

Create files like:
- `company_info.md` - Information about your company
- `faq.txt` - Frequently asked questions
- `documentation.md` - Product documentation
- `code_examples.py` - Code snippets

The agent will search these files to answer user questions!
""")
        print(f"✓ Created knowledge_base/ with sample README.md")

        # 7. Update config.yml with agent name and description
        config_file = new_agent_dir / 'config.yml'
        config_content = config_file.read_text()
        config_content = config_content.replace('name: "MyAgent"', f'name: "{name}"')

        if description:
            config_content = config_content.replace(
                'description: "AI assistant with custom knowledge and capabilities"',
                f'description: "{description}"'
            )

        config_file.write_text(config_content)
        print(f"✓ Updated config.yml with agent name{' and description' if description else ''}")

        # Success!
        print(f"\n✅ Agent '{name}' created successfully!\n")

        # Print next steps
        print("📝 Next steps:\n")
        print(f"1. Set up environment variables:")
        print(f"   cd {new_agent_dir}")
        print(f"   cp .env.example .env")
        print(f"   # Edit .env with your API keys\n")

        print(f"2. Add knowledge to knowledge_base/:")
        print(f"   # Add .txt, .md, or other files to knowledge_base/\n")

        print(f"3. Customize config.yml:")
        print(f"   # Edit personality, models, knowledge sources, channels\n")

        print(f"4. Run your agent:")
        print(f"   python agent.py\n")

        print("📚 See AGENT_TEMPLATE.md for detailed documentation\n")

        return True

    except Exception as e:
        print(f"\n❌ Error creating agent: {e}")
        # Clean up partial creation
        if new_agent_dir.exists():
            shutil.rmtree(new_agent_dir)
        return False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Create a new AI agent from template',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_agent.py support_bot
  python create_agent.py blog_assistant --description "AI assistant for blog content"
  python create_agent.py sales_ai --description "Sales and customer support AI"
        """
    )

    parser.add_argument('name', help='Agent name (alphanumeric with _ or -)')
    parser.add_argument('--description', '-d', help='Agent description')

    args = parser.parse_args()

    success = create_agent(args.name, args.description)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
