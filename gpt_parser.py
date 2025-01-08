import json
import openai
from typing import List, Dict, Optional

class GPTParser:
    """
    A helper class to encapsulate all GPT-related functionality.
    """

    def __init__(self, api_key: str):
        """
        Initialize GPT parser with the given API key.
        """
        openai.api_key = api_key

    async def parse_assignments_from_text(self, text: str) -> List[Dict]:
        """
        Uses GPT to parse assignment data from raw text (syllabus_body, homepage, etc.).
        Expects JSON in the format:
        [
            {"name": <str>, "due_date": <YYYY-MM-DD>, "description": <str>, "points": <float or str>},
            ...
        ]
        """
        prompt = f"""
        Extract assignment details from text. Return JSON array:
        [
          {{ "name": <str>, "due_date": <YYYY-MM-DD>, "description": <str>, "points": <float or str> }},
          ...
        ]
        Only include items with a clear date.

        TEXT:
        {text}
        """
        try:
            resp = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant that extracts assignment info."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0
            )
            raw_content = resp["choices"][0]["message"]["content"]
            return json.loads(raw_content)
        except Exception:
            # Ideally log or raise an exception for real error handling
            return []

    async def parse_syllabus_for_missing_assignments(self, text: str) -> List[Dict]:
        """
        After extracting text from a Syllabus PDF/DOCX, pass to GPT
        for potential missing assignments in Canvas. Expects JSON:
        [
          { "name": "...", "approx_date": "YYYY-MM-DD", "description": "..." },
          ...
        ]
        """
        prompt = f"""
        The following is a Syllabus from a course. Identify any assignment, quiz, or project
        not in Canvas, including approximate due dates if found. Return a JSON array like:
        [
          {{ "name": "...", "approx_date": "YYYY-MM-DD", "description": "..." }},
          ...
        ]
        Syllabus Text:
        {text}
        """
        try:
            resp = await openai.ChatCompletion.acreate(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant for missing assignment info."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            raw_json = resp["choices"][0]["message"]["content"]
            return json.loads(raw_json)
        except Exception:
            return []