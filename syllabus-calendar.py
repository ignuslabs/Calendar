import os
import re
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

# For local time conversions in ICS
from zoneinfo import ZoneInfo

# canvasapi
from canvasapi import Canvas
from canvasapi.course import Course
from canvasapi.assignment import Assignment
from canvasapi.module import ModuleItem

# Additional libs
import spacy
import openai
import PyPDF2
import docx

from dotenv import load_dotenv
from icalendar import Calendar, Event
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

import asyncio

###############################################################################
# PART 1: Manage environment variables with a .env file
###############################################################################

console = Console()
ENV_FILE = ".env"

def prompt_for_credentials() -> None:
    console.print("\n[bold][yellow]HOW TO OBTAIN REQUIRED API KEYS[/yellow][/bold]\n")
    console.print("[cyan]1) Canvas API URL & Canvas API Key:[/cyan]")
    console.print("   - Must have sufficient privileges (e.g. instructor token).")
    console.print("   - Typically https://<domain>.instructure.com (not /api/v1).")
    console.print("\n[cyan]2) OpenAI API Key:[/cyan]")
    console.print("   - https://platform.openai.com/account/api-keys\n")
    
    canvas_url = console.input("[cyan]Canvas API URL[/cyan] (e.g. https://canvas.school.edu): ").strip()
    # remove /api/v1 if present
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
    if not os.path.exists(ENV_FILE):
        console.print("[red].env file not found. Please set credentials first.[/red]\n")
        return False
    load_dotenv(dotenv_path=ENV_FILE)
    console.print("[green]Loaded environment variables from .env[/green]\n")
    return True

def delete_credentials() -> None:
    if os.path.exists(ENV_FILE):
        os.remove(ENV_FILE)
        console.print("[yellow].env file deleted.[/yellow]\n")
    else:
        console.print("[red]No .env file found to delete.[/red]\n")

###############################################################################
# PART 2: The CanvasCalendarGenerator with fallback logic for Syllabus in 
# Files, Modules, or syllabus_body/homepage
###############################################################################

class CanvasCalendarGenerator:
    def __init__(self, local_timezone: str = "UTC"):
        self.local_timezone = local_timezone
        self._load_environment()

        self.canvas = Canvas(self.canvas_api_url, self.canvas_api_key)
        self.nlp = spacy.load("en_core_web_sm")
        openai.api_key = self.openai_api_key

    def _load_environment(self):
        reqs = ["CANVAS_API_URL", "CANVAS_API_KEY", "OPENAI_API_KEY"]
        missing = []
        for var in reqs:
            val = os.getenv(var)
            if not val:
                missing.append(var)
            setattr(self, var.lower(), val)
        if missing:
            console.print(f"[red]Missing env vars: {', '.join(missing)}[/red]")
            exit(1)

        self.canvas_api_url = self.canvas_api_url
        self.canvas_api_key = self.canvas_api_key
        self.openai_api_key = self.openai_api_key

    def get_user_courses(self) -> List[Course]:
        try:
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), transient=True) as progress:
                progress.add_task(description="Fetching user courses...", total=None)
                courses = list(self.canvas.get_courses())
            if not courses:
                console.print("[red]No courses found for this user.[/red]")
                return []
            
            table = Table(title="Your Courses")
            table.add_column("Number", justify="right", style="cyan")
            table.add_column("Course Name", style="magenta")
            table.add_column("ID", style="green")
            for i, c in enumerate(courses, 1):
                name = getattr(c, "name", "Untitled")
                table.add_row(str(i), name, str(c.id))

            console.print(table)
            return courses
        except Exception as e:
            console.print(f"[red]Error fetching courses: {e}[/red]")
            return []

    async def process_course(self, course: Course) -> None:
        """
        1) fetch assignments
        2) check missing due dates
        3) manual or GPT parse
        4) generate ICS
        5) attempt to parse Syllabus from (Files or Modules or fallback)
        """
        try:
            with Progress() as progress:
                task = progress.add_task(f"Processing {course.name}...", total=100)
                
                # 1) Assignments
                progress.update(task, advance=20, description="Fetching assignments...")
                assignments = list(course.get_assignments())
                missing = [a for a in assignments if not getattr(a, "due_at", None)]

                console.print(f"\n[green]Course:[/green] {course.name}")
                console.print(f"{len(assignments)} total assignments; {len(missing)} missing due dates.\n")

                # 2) missing due dates logic
                if missing:
                    choice = console.input("Enter 'y' for manual dates, 'n' for GPT parse: ").lower()
                    if choice == 'y':
                        self._handle_manual_dates(missing)
                    elif choice == 'n':
                        progress.update(task, advance=20, description="Parsing syllabus_body/homepage for missing dates...")
                        await self._search_course_materials(course, missing)

                # 3) Generate ICS
                progress.update(task, advance=30, description="Generating ICS...")
                self._generate_calendar(assignments, course.name)

                # 4) Attempt to parse Syllabus from files or modules
                progress.update(task, advance=10, description="Searching Syllabus file...")
                await self._search_syllabus_integrated(course)

                progress.update(task, advance=20, description="Complete!")
        except Exception as ex:
            console.print(f"[red]Error processing {course.name}: {ex}[/red]")

    def _handle_manual_dates(self, assignments: List[Assignment]) -> None:
        for a in assignments:
            while True:
                dt_str = console.input(f"Enter due date for '{a.name}' (YYYY-MM-DD): ")
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d")
                except ValueError:
                    console.print("[red]Invalid format.[/red]")
                    continue

                hr = console.input("Hour [0-23, default 23]: ")
                mn = console.input("Minute [0-59, default 59]: ")
                hour = 23 if not hr else int(hr)
                minute = 59 if not mn else int(mn)
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    console.print("[red]Invalid hour or minute[/red]")
                    continue

                dt = dt.replace(hour=hour, minute=minute)
                dt_utc = dt.astimezone(timezone.utc)
                a.due_at = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                break

    async def _search_course_materials(self, course: Course, assignments: List[Assignment]) -> None:
        """
        Original approach: parse course.syllabus_body + front-page
        """
        try:
            syllabus = getattr(course, "syllabus_body", "") or ""
            homepage_text = ""
            try:
                fp = course.get_page("front-page")
                if fp and fp.body:
                    homepage_text = fp.body
            except:
                pass

            combined = syllabus + "\n\n" + homepage_text
            gpt_data = await self._parse_dates_with_gpt(combined)
            if gpt_data:
                self._match_assignments_with_dates(assignments, gpt_data)
        except Exception as e:
            console.print(f"[red]Error searching course materials: {e}[/red]")

    async def _parse_dates_with_gpt(self, text: str) -> List[Dict]:
        try:
            prompt = f"""
            Extract assignment details from the text. Return JSON array:
            [
              {{
                "name": <str>,
                "due_date": <YYYY-MM-DD>,
                "description": <str>,
                "points": <str or float if found>
              }},
              ...
            ]
            Only include items with a clear date.

            TEXT:
            {text}
            """
            resp = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts assignment info."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0
            )
            raw = resp["choices"][0]["message"]["content"]
            return json.loads(raw)
        except Exception as ex:
            console.print(f"[red]GPT parse error: {ex}[/red]")
            return []

    def _match_assignments_with_dates(self, assignments: List[Assignment], data: List[Dict]) -> None:
        for a in assignments:
            if getattr(a, "due_at", None):
                continue
            best_score = 0.0
            best_item = None
            for item in data:
                gpt_name = item.get("name", "")
                if not a.name or not gpt_name:
                    continue
                score = self.nlp(a.name).similarity(self.nlp(gpt_name))
                if score > best_score:
                    best_score = score
                    best_item = item
            if best_item:
                due_date = best_item.get("due_date")
                desc = best_item.get("description")
                if desc:
                    a.description = desc
                if due_date:
                    a.due_at = f"{due_date}T23:59:00Z"

    def _generate_calendar(self, assignments: List[Assignment], course_name: str) -> None:
        cal = Calendar()
        cal.add("prodid", "-//CanvasCalendar//EN")
        cal.add("version", "2.0")

        local_zone = ZoneInfo(self.local_timezone)

        for a in assignments:
            if not a.due_at:
                continue
            try:
                dt_utc = datetime.strptime(a.due_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            dt_local = dt_utc.astimezone(local_zone)

            event = Event()
            event.add("summary", f"{course_name} - {a.name}")
            desc = getattr(a, "description", "") or ""
            event.add("description", desc)

            event.add("dtstart", dt_local)
            event["DTSTART"].params["TZID"] = self.local_timezone

            dt_end = dt_local + timedelta(hours=1)
            event.add("dtend", dt_end)
            event["DTEND"].params["TZID"] = self.local_timezone

            cal.add_component(event)

        filename = f"{course_name.replace(' ', '_')}_calendar.ics"
        with open(filename, "wb") as f:
            f.write(cal.to_ical())
        console.print(f"[green]Calendar saved as {filename}[/green]\n")

    ############################################################################
    # Part: Syllabus Integration from Files, Modules, or fallback
    ############################################################################

    async def _search_syllabus_integrated(self, course: Course):
        """
        1) Try course.get_files() for 'Syllabus'
        2) If not found or locked, try modules
        3) If all fails, fallback to course.syllabus_body/homepage (already done).
        """
        # A) Try normal files approach
        file_obj = self._find_syllabus_file(course)
        if file_obj:
            local_path = self._download_extract(file_obj)
            if local_path:
                await self._parse_syllabus_with_gpt(local_path)
                return

        # B) If not found, try modules approach
        item = self._find_syllabus_via_modules(course)
        if item:
            try:
                f_obj = item.get_file()
                local_path = self._download_extract(f_obj)
                if local_path:
                    await self._parse_syllabus_with_gpt(local_path)
                    return
            except Exception as e:
                console.print(f"[red]Module Syllabus locked or error: {e}[/red]")

        # C) If still not found, we rely on the normal _search_course_materials fallback
        console.print("[yellow]No Syllabus file found via Files or Modules (or locked). Already parsed course.syllabus_body/homepage above.[/yellow]")

    def _find_syllabus_file(self, course: Course):
        """
        Look in course.get_files() for 'Syllabus' in the filename or display_name
        """
        try:
            files = course.get_files()
            for f in files:
                fname = (f.display_name or f.filename).lower()
                if "syllabus" in fname:
                    return f
            return None
        except:
            return None

    def _find_syllabus_via_modules(self, course: Course) -> Optional[ModuleItem]:
        """
        Search each module for a module item of type=File with 'Syllabus' in the title
        Return the first match or None
        """
        try:
            mods = course.get_modules()
            for m in mods:
                items = m.get_module_items()
                for it in items:
                    if it.type == "File" and "syllabus" in it.title.lower():
                        return it  # return the first match
            return None
        except:
            return None

    def _download_extract(self, file_obj) -> Optional[str]:
        """
        Download the file (PDF or docx) and extract text to a .txt file.
        Return path to the .txt or None on failure.
        """
        try:
            fname = file_obj.filename
            console.print(f"Downloading Syllabus file: {fname}")
            content = file_obj.get_contents()

            with open(fname, "wb") as f:
                f.write(content)

            # Check extension
            if fname.lower().endswith(".pdf"):
                text = self._extract_pdf(fname)
            elif fname.lower().endswith(".docx"):
                text = self._extract_docx(fname)
            else:
                console.print("[yellow]Unsupported Syllabus file format (not PDF/DOCX).[/yellow]")
                return None

            if not text:
                console.print("[red]No text extracted from file[/red]")
                return None

            txt_path = fname + ".txt"
            with open(txt_path, "w", encoding="utf-8") as tf:
                tf.write(text)
            return txt_path
        except Exception as ex:
            console.print(f"[red]Error downloading/extracting file: {ex}[/red]")
            return None

    def _extract_pdf(self, path: str) -> str:
        text = ""
        with open(path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                txt = page.extract_text() or ""
                text += txt + "\n"
        return text

    def _extract_docx(self, path: str) -> str:
        document = docx.Document(path)
        return "\n".join(p.text for p in document.paragraphs)

    async def _parse_syllabus_with_gpt(self, local_txt_path: str):
        """
        Read the .txt, send it to GPT to look for extra assignments.
        """
        try:
            with open(local_txt_path, "r", encoding="utf-8", errors="ignore") as f:
                raw_text = f.read()
            
            prompt = f"""
            The following is a Syllabus from a course. Identify any assignment, quiz, or project 
            not in Canvas, including approximate due dates. Return an array in JSON, e.g.:
            [
              {{ "name": "...", "approx_date": "YYYY-MM-DD", "description": "..." }},
              ...
            ]

            SYLLABUS TEXT:
            {raw_text}
            """
            resp = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant for missing assignment info."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0
            )
            raw_json = resp["choices"][0]["message"]["content"]
            data = json.loads(raw_json)
            if data:
                console.print("[green]GPT found potential additional assignments in Syllabus![/green]")
                # You could integrate these with your existing assignment list if desired.
        except Exception as ex:
            console.print(f"[red]Error parsing Syllabus with GPT: {ex}[/red]")

###############################################################################
# PART 3: Main Flow
###############################################################################

async def run_canvas_flow():
    console.print("\n[bold]Enter local time zone[/bold] (e.g. America/New_York, UTC, Europe/London)")
    tz = console.input("Time Zone: ").strip()
    if not tz:
        tz = "UTC"

    generator = CanvasCalendarGenerator(local_timezone=tz)
    courses = generator.get_user_courses()
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
                await generator.process_course(c)
            else:
                console.print("[red]Invalid selection[/red]")
        except ValueError:
            console.print("[red]Please enter a number[/red]")

async def main():
    while True:
        console.print("\n[bold]Main Menu[/bold]")
        console.print("1) Set .env credentials")
        console.print("2) Load .env credentials")
        console.print("3) Delete .env file")
        console.print("4) Start Canvas Calendar Flow (with Modules + Syllabus fallback)")
        console.print("5) Exit")

        choice = console.input("Choice: ").strip()
        if choice == "1":
            prompt_for_credentials()
        elif choice == "2":
            load_credentials()
        elif choice == "3":
            delete_credentials()
        elif choice == "4":
            if load_credentials():
                await run_canvas_flow()
        elif choice == "5":
            console.print("[green]Exiting...[/green]")
            break
        else:
            console.print("[red]Invalid choice. Try again.[/red]")

if __name__ == "__main__":
    asyncio.run(main())