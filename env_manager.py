import os
import re
from dotenv import load_dotenv
from rich.console import Console

console = Console()
ENV_FILE = ".env"

def prompt_for_credentials() -> None:
    """
    Prompt for Canvas URL/Key + OpenAI Key, store in .env
    """
    console.print("\n[bold][yellow]HOW TO OBTAIN REQUIRED API KEYS[/yellow][/bold]\n")
    console.print("[cyan]1) Canvas API URL & Canvas API Key:[/cyan]")
    console.print("   - Typically https://<your_domain>.instructure.com (no /api/v1).")
    console.print("   - Token from 'Account > Settings > New Access Token'.\n")
    console.print("[cyan]2) OpenAI API Key:[/cyan]")
    console.print("   - https://platform.openai.com/account/api-keys\n")
    
    canvas_url = console.input("[cyan]Canvas API URL[/cyan] (e.g. https://canvas.school.edu): ").strip()
    # Clean up trailing /api/v1 if needed
    canvas_url_clean = re.sub(r"/api/v1/?$", "", canvas_url, flags=re.IGNORECASE).rstrip("/")
    if canvas_url != canvas_url_clean:
        console.print(f"[yellow]Stripped '/api/v1' from the URL: {canvas_url_clean}[/yellow]")

    canvas_key = console.input("[cyan]Canvas API Key[/cyan]: ").strip()
    openai_key = console.input("[cyan]OpenAI API Key[/cyan]: ").strip()

    with open(ENV_FILE, 'w', encoding='utf-8') as env_file:
        env_file.write(f"CANVAS_API_URL={canvas_url_clean}\n")
        env_file.write(f"CANVAS_API_KEY={canvas_key}\n")
        env_file.write(f"OPENAI_API_KEY={openai_key}\n")

    console.print("[green]Credentials saved to .env[/green]\n")

def load_credentials() -> bool:
    """
    Load credentials from .env into environment variables. 
    Return True if successful, False if .env doesn't exist.
    """
    if not os.path.exists(ENV_FILE):
        console.print("[red].env file not found. Please set credentials first (Option 1).[/red]\n")
        return False
    load_dotenv(dotenv_path=ENV_FILE)
    console.print("[green]Loaded environment variables from .env[/green]\n")
    return True

def delete_credentials() -> None:
    """
    Delete the .env file if it exists.
    """
    if os.path.exists(ENV_FILE):
        os.remove(ENV_FILE)
        console.print("[yellow].env file deleted.[/yellow]\n")
    else:
        console.print("[red]No .env file found to delete.[/red]\n")