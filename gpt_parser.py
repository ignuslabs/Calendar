import json
import openai
from typing import List, Dict

class GPTParser:
    """
    A helper class to encapsulate GPT-related functionality.
    """

    def __init__(self, api_key: str):
        """
        Initialize GPT parser with the given API key.
        """
        openai.api_key = api_key

    async def parse_assignments_from_text(self, text: str) -> List[Dict]:
        """
        Uses GPT to parse assignment data from raw text (syllabus_body, homepage, 
        module files, etc.). We expect JSON in the format:
        [
            {"name": <str>, "due_date": <YYYY-MM-DD>, "description": <str>, "points": <float or str>},
            ...
        ]
        """
        # Feel free to tweak the prompt or use system instructions, examples, etc.
        prompt = f"""
        Extract assignment details from text. Return JSON array:
        [
          {{ "name": <str>, "due_date": <YYYY-MM-DD>, "description": <str>, "points": <float or str> }},
          ...
        ]
        Only include items that clearly have a date.

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
            # In a real scenario, you'd handle/log the exception more robustly
            return []