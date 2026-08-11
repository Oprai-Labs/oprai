#!/usr/bin/env python3
"""
OPRAI CLI Tools

Similar to elizaos CLI: `oprai create`, `oprai start`, etc.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import click

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """OPRAI - DeFi-native AI assistant for Solana"""
    pass


# ==================== Character Commands ====================

@cli.group()
def character():
    """Manage AI characters"""
    pass


@character.command("create")
@click.option("--name", "-n", required=True, help="Character name")
@click.option("--provider", "-p", default="openai", help="LLM provider")
@click.option("--template", "-t", default="default", help="Character template")
@click.option("--output", "-o", default="characters", help="Output directory")
def create_character(name: str, provider: str, template: str, output: str):
    """Create a new character file"""
    from app.models.character import BUILTIN_CHARACTERS

    output_dir = Path(output)
    output_dir.mkdir(exist_ok=True)

    # Find template
    char_template = None
    for t in BUILTIN_CHARACTERS:
        if t.get("name", "").lower() == template.lower():
            char_template = t
            break

    if not char_template:
        char_template = BUILTIN_CHARACTERS[0]

    # Create character
    character_data = {
        "version": "1.0.0",
        "name": name,
        "modelProvider": provider,
        "clients": char_template.get("clients", ["direct"]),
        "bio": char_template.get("bio", []),
        "lore": char_template.get("lore", []),
        "topics": char_template.get("topics", []),
        "adjectives": char_template.get("adjectives", []),
        "style": char_template.get("style", {"all": [], "chat": [], "post": []}),
    }

    filename = name.lower().replace(" ", "_") + ".json"
    output_path = output_dir / filename

    with open(output_path, "w") as f:
        json.dump(character_data, f, indent=2)

    click.echo(f"✅ Created character: {output_path}")


@character.command("list")
@click.option("--dir", "-d", default="characters", help="Characters directory")
def list_characters(dir: str):
    """List all characters"""
    char_dir = Path(dir)
    if not char_dir.exists():
        click.echo(f"Directory not found: {dir}")
        return

    for char_file in char_dir.glob("*.json"):
        with open(char_file) as f:
            data = json.load(f)
        click.echo(f"  • {data.get('name', char_file.stem)} ({char_file.name})")


@character.command("validate")
@click.argument("file", type=click.Path(exists=True))
def validate_character(file: str):
    """Validate a character file"""
    from app.models.character import Character

    with open(file) as f:
        data = json.load(f)

    try:
        char = Character(**data)
        click.echo(f"✅ Valid character: {char.name}")
        click.echo(f"   Provider: {char.model_provider}")
        click.echo(f"   Clients: {', '.join(char.clients)}")
        click.echo(f"   Topics: {', '.join(char.topics or [])}")
    except Exception as e:
        click.echo(f"❌ Invalid character: {e}", err=True)


# ==================== Knowledge Commands ====================

@cli.group()
def knowledge():
    """Knowledge base management tools"""
    pass


@knowledge.command("import-folder")
@click.argument("folder", type=click.Path(exists=True))
@click.option("--character", "-c", help="Target character file")
@click.option("--output", "-o", default="knowledge", help="Output directory")
def import_folder(folder: str, character: Optional[str], output: str):
    """Convert folder contents to knowledge base (folder2knowledge)"""
    from app.ingestion import DocumentIngestionService

    folder_path = Path(folder)
    output_dir = Path(output)
    output_dir.mkdir(exist_ok=True)

    click.echo(f"📂 Processing folder: {folder_path}")

    # Process all files
    all_knowledge = []

    for file_path in folder_path.rglob("*"):
        if file_path.is_file() and file_path.suffix in [".txt", ".md", ".json", ".pdf"]:
            click.echo(f"  Processing: {file_path.name}")

            try:
                # Read and chunk
                if file_path.suffix == ".pdf":
                    import PyPDF2
                    with open(file_path, "rb") as f:
                        reader = PyPDF2.PdfReader(f)
                        text = "\n".join(page.extract_text() or "" for page in reader.pages)
                else:
                    text = file_path.read_text()

                # Create knowledge chunks
                chunks = _chunk_text(text, chunk_size=500)
                for i, chunk in enumerate(chunks):
                    all_knowledge.append({
                        "content": chunk,
                        "source": str(file_path),
                        "chunk": i,
                    })

            except Exception as e:
                click.echo(f"  ⚠️  Skipped: {e}")

    # Save knowledge file
    output_file = output_dir / f"{folder_path.name}_knowledge.json"
    with open(output_file, "w") as f:
        json.dump(all_knowledge, f, indent=2)

    click.echo(f"\n✅ Created knowledge file: {output_file}")
    click.echo(f"   Total chunks: {len(all_knowledge)}")

    # Optionally merge into character
    if character:
        _merge_knowledge_to_character(character, all_knowledge)


@knowledge.command("import-tweets")
@click.argument("file", type=click.Path(exists=True))
@click.option("--character", "-c", required=True, help="Target character file")
def import_tweets(file: str, character: str):
    """Import tweets to character (tweets2character)"""
    with open(file) as f:
        tweets = json.load(f)

    if not isinstance(tweets, list):
        click.echo("Expected JSON array of tweets")
        return

    # Extract post examples
    post_examples = []
    for tweet in tweets[:100]:  # Limit to 100
        text = tweet.get("text", tweet.get("full_text", ""))
        if text and len(text) > 20:
            post_examples.append(text)

    # Load and update character
    with open(character) as f:
        char_data = json.load(f)

    char_data["postExamples"] = char_data.get("postExamples", []) + post_examples

    with open(character, "w") as f:
        json.dump(char_data, f, indent=2)

    click.echo(f"✅ Added {len(post_examples)} post examples to {character}")


@knowledge.command("merge")
@click.argument("knowledge_file", type=click.Path(exists=True))
@click.argument("character_file", type=click.Path(exists=True))
def merge_knowledge(knowledge_file: str, character_file: str):
    """Merge knowledge file into character (knowledge2character)"""
    with open(knowledge_file) as f:
        knowledge = json.load(f)

    _merge_knowledge_to_character(character_file, knowledge)


def _merge_knowledge_to_character(character_file: str, knowledge: list):
    """Merge knowledge into character file"""
    with open(character_file) as f:
        char_data = json.load(f)

    # Extract knowledge content
    knowledge_list = []
    for item in knowledge:
        if isinstance(item, dict):
            knowledge_list.append(item.get("content", ""))
        elif isinstance(item, str):
            knowledge_list.append(item)

    # Merge
    existing = char_data.get("knowledge", [])
    char_data["knowledge"] = existing + knowledge_list

    with open(character_file, "w") as f:
        json.dump(char_data, f, indent=2)

    click.echo(f"✅ Merged {len(knowledge_list)} knowledge items into {character_file}")


def _chunk_text(text: str, chunk_size: int = 500) -> list[str]:
    """Split text into chunks"""
    words = text.split()
    chunks = []

    current_chunk = []
    current_size = 0

    for word in words:
        current_chunk.append(word)
        current_size += len(word) + 1

        if current_size >= chunk_size:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
            current_size = 0

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


# ==================== Agent Commands ====================

@cli.group()
def agent():
    """Manage AI agents"""
    pass


@agent.command("start")
@click.argument("character_file", type=click.Path(exists=True))
@click.option("--port", "-p", default=3020, help="Service port")
def start_agent(character_file: str, port: int):
    """Start an agent with a character file"""
    import uvicorn

    click.echo(f"🚀 Starting agent with: {character_file}")
    click.echo(f"   Port: {port}")

    # Set environment variable for character
    import os
    os.environ["OPRAI_CHARACTER_FILE"] = character_file

    # Start service
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )


@agent.command("chat")
@click.argument("character_file", type=click.Path(exists=True))
@click.option("--message", "-m", help="Send a single message")
def chat_agent(character_file: str, message: Optional[str]):
    """Interactive chat with an agent"""
    from app.models.character import Character
    from app.services.character import PromptBuilder

    with open(character_file) as f:
        char_data = json.load(f)

    character = Character(**char_data)
    builder = PromptBuilder(character)

    if message:
        # Single message mode
        click.echo(f"\n{character.name}: Thinking...")
        # Would call LLM here
        click.echo(f"\n{character.name}: [Response would be generated by LLM]")
    else:
        # Interactive mode
        click.echo(f"\n💬 Chatting with {character.name}")
        click.echo("Type 'quit' to exit\n")

        while True:
            try:
                user_input = click.prompt("You", type=str)
                if user_input.lower() == "quit":
                    break

                click.echo(f"\n{character.name}: [Response would be generated by LLM]\n")
            except KeyboardInterrupt:
                break


# ==================== Plugin Commands ====================

@cli.group()
def plugin():
    """Manage plugins"""
    pass


@plugin.command("list")
def list_plugins():
    """List all available plugins"""
    click.echo("\n📦 Available Plugins:\n")
    click.echo("  Core:")
    click.echo("    • jupiter - Jupiter DEX aggregator")
    click.echo("    • orca - Orca Whirlpools")
    click.echo("    • kamino - Kamino Finance")
    click.echo("    • jito - Jito liquid staking")
    click.echo("    • raydium - Raydium AMM")
    click.echo("    • marinade - Marinade Finance")
    click.echo("    • meteora - Meteora pools")
    click.echo("\n  Social:")
    click.echo("    • twitter - Twitter/X client")
    click.echo("    • discord - Discord client")
    click.echo("    • telegram - Telegram client")
    click.echo("    • farcaster - Farcaster client")


@plugin.command("install")
@click.argument("plugin_name")
def install_plugin(plugin_name: str):
    """Install a plugin (placeholder)"""
    click.echo(f"📦 Installing plugin: {plugin_name}")
    click.echo("   Note: Plugin installation not yet implemented")
    click.echo("   Add plugins manually to the plugins directory")


# ==================== Project Commands ====================

@cli.command("init")
@click.argument("project_name")
def init_project(project_name: str):
    """Initialize a new OPRAI project"""
    project_dir = Path(project_name)
    project_dir.mkdir(exist_ok=True)

    # Create structure
    (project_dir / "characters").mkdir(exist_ok=True)
    (project_dir / "knowledge").mkdir(exist_ok=True)
    (project_dir / "plugins").mkdir(exist_ok=True)

    # Create .env
    env_content = """# OPRAI Configuration
OPRAI_OPENAI_API_KEY=your_key_here
OPRAI_JWT_SECRET=your_secret_here
OPRAI_INTERNAL_API_KEY=your_api_key_here

# Optional: Platform API Keys
TWITTER_API_KEY=
DISCORD_BOT_TOKEN=
TELEGRAM_BOT_TOKEN=
FARCASTER_API_KEY=
"""
    (project_dir / ".env.example").write_text(env_content)

    # Create default character
    default_char = {
        "version": "1.0.0",
        "name": "Default Agent",
        "modelProvider": "openai",
        "clients": ["direct"],
        "bio": ["A helpful AI assistant."],
        "topics": ["general"],
        "adjectives": ["helpful", "friendly"],
        "style": {"all": ["Be helpful and concise"], "chat": [], "post": []},
    }
    with open(project_dir / "characters" / "default.json", "w") as f:
        json.dump(default_char, f, indent=2)

    click.echo(f"✅ Created project: {project_dir}")
    click.echo("\nNext steps:")
    click.echo(f"  1. cd {project_name}")
    click.echo("  2. Copy .env.example to .env and add your API keys")
    click.echo("  3. Run: oprai agent start characters/default.json")


@cli.command("health")
def health_check():
    """Check service health"""
    import httpx

    services = [
        ("Gateway", "http://localhost:3001/health"),
        ("Auth", "http://localhost:3010/health"),
        ("Chat", "http://localhost:3020/health"),
        ("Solana", "http://localhost:3030/health"),
        ("Memory", "http://localhost:3040/health"),
    ]

    click.echo("\n🏥 Service Health:\n")

    for name, url in services:
        try:
            response = httpx.get(url, timeout=5.0)
            if response.status_code == 200:
                click.echo(f"  ✅ {name}: OK")
            else:
                click.echo(f"  ⚠️  {name}: Status {response.status_code}")
        except Exception as e:
            click.echo(f"  ❌ {name}: Unavailable")


if __name__ == "__main__":
    cli()
