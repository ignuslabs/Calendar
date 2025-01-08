# main.py

import asyncio
import os
from rich.console import Console

# Local imports
from env_manager import (
    prompt_for_credentials,
    load_credentials,
    delete_credentials
)
from canvas_calendar_generator import CanvasCalendarGenerator

console = Console()

async def run_canvas_flow():
    """
    Ask for local timezone, instantiate CanvasCalendarGenerator,
    list courses, and let user pick a course to process.
    """
    console.print("\n[bold]Enter local time zone[/bold] (e.g. America/New_York, UTC, Europe/London)")
    tz = console.input("Time Zone: ").strip()
    if not tz:
        tz = "UTC"

    # Load environment variables
    canvas_url = os.getenv("CANVAS_API_URL", "")
    canvas_key = os.getenv("CANVAS_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    # Instantiate the generator
    gen = CanvasCalendarGenerator(
        canvas_api_url=canvas_url,
        canvas_api_key=canvas_key,
        openai_api_key=openai_key,
        local_timezone=tz
    )

    # Fetch user courses
    courses = gen.get_user_courses()
    if not courses:
        return

    while True:
        pick = console.input("\nPick a course number or 0 to exit: ")
        try:
            idx = int(pick)
            if idx == 0:
                break
            if 1 <= idx <= len(courses):
                c = courses[idx - 1]
                await gen.process_course(c)
            else:
                console.print("[red]Invalid selection[/red]")
        except ValueError:
            console.print("[red]Please enter a number[/red]")

async def main():
    """
    Main menu for the application.
    """
    while True:
        console.print("\n[bold]Main Menu[/bold]")
        console.print("1) Set .env credentials")
        console.print("2) Load .env credentials")
        console.print("3) Delete .env file")
        console.print("4) Start Canvas Calendar Flow (Syllabus + GPT + ICS)")
        console.print("5) Exit")

        choice = console.input("Choice: ").strip()
        if choice == "1":
            prompt_for_credentials()
        elif choice == "2":
            load_credentials()
        elif choice == "3":
            delete_credentials()
        elif choice == "4":
            # Load credentials before running the flow
            if load_credentials():
                await run_canvas_flow()
        elif choice == "5":
            console.print("[green]Exiting...[/green]")
            break
        else:
            console.print("[red]Invalid choice. Try again.[/red]")

if __name__ == "__main__":
    asyncio.run(main())