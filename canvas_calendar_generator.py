import os
import asyncio
import spacy
import PyPDF2
import docx

from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from canvasapi import Canvas
from canvasapi.course import Course
from canvasapi.assignment import Assignment
from canvasapi.file import File as CanvasFile
from canvasapi.module import Module, ModuleItem

from icalendar import Calendar, Event
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

# Local imports
from gpt_parser import GPTParser

console = Console()

class CanvasCalendarGenerator:
    """
    Revised CanvasCalendarGenerator that ensures we:
    1) Fetch Canvas assignments
    2) Ensure we check for a "Syllabus" via course.syllabus_body first
    3) If not found, gather text from front page, course files, and modules
    4) Pass that text to GPT
    5) Cross-reference GPT results with assignments
    6) Prompt for missing data
    7) Generate ICS after final data is ready
    """

    def __init__(self,
                 canvas_api_url: str,
                 canvas_api_key: str,
                 openai_api_key: str,
                 local_timezone: str = "UTC"):
        self.canvas_api_url = canvas_api_url
        self.canvas_api_key = canvas_api_key
        self.openai_api_key = openai_api_key
        self.local_timezone = local_timezone

        # Initialize Canvas
        self.canvas = Canvas(self.canvas_api_url, self.canvas_api_key)

        # Initialize GPT parser
        self.gpt_parser = GPTParser(self.openai_api_key)

        # SpaCy model for name similarity
        self.nlp = spacy.load("en_core_web_sm")

    def get_user_courses(self) -> List[Course]:
        """
        Retrieve and display courses for the current user.
        """
        try:
            # We can show a quick progress for courses
            with Progress(transient=True) as progress:
                task = progress.add_task("Fetching user courses...", total=None)
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
        Full workflow for a single course:
        1) Fetch Canvas assignments
        2) Gather text from course.syllabus_body if present
        3) Gather additional text from front page, files, modules if needed
        4) GPT parse that text
        5) Cross-reference results with assignments
        6) Prompt user for missing data
        7) Generate ICS
        """
        try:
            console.print(f"\n[bold green]Course:[/bold green] {course.name}\n")

            # Use a single progress bar for major steps
            with Progress(transient=True) as progress:
                task = progress.add_task(f"Processing {course.name}...", total=5)

                # Step 1: Fetch assignments
                progress.update(task, advance=1, description="Fetching assignments...")
                assignments = list(course.get_assignments())
                console.print(f"{len(assignments)} total assignments found.")

                # Step 2: Check if course.syllabus_body has content
                progress.update(task, advance=1, description="Checking course syllabus...")
                raw_syllabus_body = getattr(course, "syllabus_body", "") or ""

                # Step 3: If syllabus_body is empty, gather more text
                progress.update(task, advance=1, description="Gathering additional text...")
                if raw_syllabus_body.strip():
                    # We already have some textual content
                    all_text = raw_syllabus_body
                else:
                    # Fallback: gather from front page, files, modules
                    all_text = await self._gather_additional_text(course)
                
                # Step 4: GPT parse if we have text
                gpt_data = []
                if all_text.strip():
                    progress.update(task, advance=1, description="Parsing text with GPT...")
                    gpt_data = await self.gpt_parser.parse_assignments_from_text(all_text)
                else:
                    console.print("[yellow]No textual materials found. Skipping GPT parse.[/yellow]")
                    progress.update(task, advance=1)

                # Step 5: Cross-reference
                progress.update(task, advance=1, description="Cross-referencing assignments...")
                self._match_assignments_with_dates(assignments, gpt_data)

            # Step 6: Prompt user for missing data
            missing = [a for a in assignments if not getattr(a, "due_at", None)]
            if missing:
                console.print(f"\n[bold][yellow]{len(missing)} assignments are still missing due dates.[/yellow][/bold]")
                choice = console.input("[cyan]Enter 'y' to manually enter them, or any other key to skip: [/cyan]").lower().strip()
                if choice == 'y':
                    self._handle_manual_dates(missing)

            # Step 7: Generate ICS
            with Progress(transient=True) as progress:
                t2 = progress.add_task("Generating ICS file...", total=None)
                self._generate_calendar(assignments, course.name)

            console.print("[green]Done processing course.[/green]\n")

        except Exception as ex:
            console.print(f"[red]Error processing {course.name}: {ex}[/red]")

    async def _gather_additional_text(self, course: Course) -> str:
        """
        If course.syllabus_body is empty, gather text from:
          - front page
          - course files (.pdf, .docx, .txt)
          - modules
        Return a combined string of text.
        """
        text_parts = []

        # A) Front page
        fp_text = self._get_front_page_text(course)
        if fp_text:
            text_parts.append(fp_text)

        # B) Course files
        file_text = await self._gather_file_texts(course)
        if file_text:
            text_parts.append(file_text)

        # C) Modules
        module_text = await self._gather_module_texts(course)
        if module_text:
            text_parts.append(module_text)

        # Combine everything
        return "\n\n".join(text_parts)

    def _get_front_page_text(self, course: Course) -> str:
        """
        Return text from front page if it exists, else empty string.
        """
        try:
            front_page = course.get_page("front-page")
            if front_page and front_page.body:
                return front_page.body
        except:
            pass
        return ""

    async def _gather_file_texts(self, course: Course) -> str:
        """
        Look through the course files. If a file extension is .pdf, .docx, or .txt,
        download & extract text. Return the combined text from all relevant files.
        """
        text_chunks = []
        try:
            file_list = list(course.get_files())
        except:
            return ""

        relevant_exts = [".pdf", ".docx", ".txt"]

        for f_obj in file_list:
            fname = (f_obj.display_name or f_obj.filename).lower()
            if any(fname.endswith(ext) for ext in relevant_exts):
                extracted = self._download_and_extract_file(f_obj)
                if extracted:
                    text_chunks.append(extracted)

        return "\n\n".join(text_chunks)

    async def _gather_module_texts(self, course: Course) -> str:
        """
        Look through modules. Download any PDF/DOCX/TXT files found. 
        You can optionally filter module names or item titles if needed.
        Return combined text from all found items.
        """
        text_chunks = []
        try:
            modules = list(course.get_modules())
        except:
            return ""

        for mod in modules:
            items = list(mod.get_module_items())
            for it in items:
                # If the item is a file, we can parse it
                if it.type == "File":
                    f_obj = it.get_file()
                    fname = (f_obj.display_name or f_obj.filename).lower()
                    if any(fname.endswith(ext) for ext in [".pdf", ".docx", ".txt"]):
                        extracted = self._download_and_extract_file(f_obj)
                        if extracted:
                            text_chunks.append(extracted)

        return "\n\n".join(text_chunks)

    def _download_and_extract_file(self, file_obj) -> str:
        """
        Download a PDF, DOCX, or TXT file from Canvas, and return extracted text.
        """
        try:
            fname = file_obj.filename
            console.print(f"Downloading file: {fname}")
            content = file_obj.get_contents()

            if isinstance(content, str):
                content = content.encode("utf-8")

            with open(fname, "wb") as f:
                f.write(content)

            # Extract text by extension
            fname_lower = fname.lower()
            if fname_lower.endswith(".pdf"):
                return self._extract_pdf_text(fname)
            elif fname_lower.endswith(".docx"):
                return self._extract_docx_text(fname)
            elif fname_lower.endswith(".txt"):
                with open(fname, "r", encoding="utf-8", errors="ignore") as tf:
                    return tf.read()
            else:
                return ""
        except Exception as ex:
            console.print(f"[red]Error downloading/extracting file '{file_obj.filename}': {ex}[/red]")
            return ""

    def _extract_pdf_text(self, pdf_path: str) -> str:
        """
        Extract text from a PDF using PyPDF2.
        """
        text = ""
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                p_txt = page.extract_text() or ""
                text += p_txt + "\n"
        return text

    def _extract_docx_text(self, docx_path: str) -> str:
        """
        Extract text from a DOCX file using python-docx.
        """
        d = docx.Document(docx_path)
        return "\n".join(p.text for p in d.paragraphs)

    def _match_assignments_with_dates(self, assignments: List[Assignment], gpt_data: List[Dict]) -> None:
        """
        Cross-reference GPT output with actual Canvas assignments via SpaCy similarity,
        then update the due date if found. Example GPT structure:
        [
          {"name": "Assignment 1", "due_date": "2025-09-01", "description": "...", "points": 100},
          ...
        ]
        """
        for a in assignments:
            # Skip if it already has a due date
            if getattr(a, "due_at", None):
                continue

            best_score = 0.0
            best_item = None
            for item in gpt_data:
                gpt_name = item.get("name", "")
                if not a.name or not gpt_name:
                    continue
                score = self.nlp(a.name).similarity(self.nlp(gpt_name))
                if score > best_score:
                    best_score = score
                    best_item = item
            if best_item:
                dd = best_item.get("due_date")
                desc = best_item.get("description")
                if desc:
                    a.description = desc
                if dd:
                    self._apply_local_utc_date(a, dd)

    def _apply_local_utc_date(self, assignment: Assignment, date_str: str) -> None:
        """
        Convert date_str (YYYY-MM-DD) into local timezone (23:59),
        then store as assignment.due_at in UTC.
        """
        try:
            local_zone = ZoneInfo(self.local_timezone)
            dt_local = datetime.strptime(date_str, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=local_zone)
            dt_utc = dt_local.astimezone(timezone.utc)
            assignment.due_at = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass

    def _handle_manual_dates(self, assignments: List[Assignment]) -> None:
        """
        Prompt the user to manually enter due dates for assignments still missing them.
        """
        for a in assignments:
            while True:
                dt_str = console.input(f"Enter due date for '{a.name}' (YYYY-MM-DD or leave blank to skip): ").strip()
                if not dt_str:
                    break

                date_parsed = self._try_parse_date(dt_str)
                if not date_parsed:
                    console.print("[red]Invalid date format.[/red]")
                    continue

                hr_str = console.input("[grey]Hour [0-23, default 23]: [/grey]").strip()
                mn_str = console.input("[grey]Minute [0-59, default 59]: [/grey]").strip()
                hour = 23 if not hr_str else int(hr_str)
                minute = 59 if not mn_str else int(mn_str)
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    console.print("[red]Invalid hour/minute[/red]")
                    continue

                date_parsed = date_parsed.replace(hour=hour, minute=minute)
                dt_utc = date_parsed.astimezone(timezone.utc)
                a.due_at = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                break

    def _try_parse_date(self, date_str: str) -> Optional[datetime]:
        """
        Attempt to parse a date string in multiple formats.
        Return a datetime object in the local time zone if successful, else None.
        """
        formats = ["%Y-%m-%d", "%m/%d/%Y"]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                local_zone = ZoneInfo(self.local_timezone)
                dt = dt.replace(tzinfo=local_zone)
                return dt
            except ValueError:
                continue
        return None

    def _generate_calendar(self, assignments: List[Assignment], course_name: str) -> None:
        """
        Create .ics file for the course, converting UTC times to the user's local timezone.
        """
        cal = Calendar()
        cal.add("prodid", "-//Canvas Calendar//EN")
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