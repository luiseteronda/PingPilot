# app/llm_change.py
from typing import Literal
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain_google_genai import ChatGoogleGenerativeAI

class Verdict(BaseModel):
    material_change: bool = Field(..., description="True only if a human would care about this change.")
    severity: Literal["none", "low", "medium", "high"] = "none"
    summary_short: str = Field(..., description="<= 180 chars. Summarize ONLY the differences (what was added/removed/updated). No restating unchanged content.")

def build_gemini_chain(model: str = "gemini-1.5-flash"):
    llm = ChatGoogleGenerativeAI(model=model, temperature=0.2)
    parser = PydanticOutputParser(pydantic_object=Verdict)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a change analyst. Output focuses ONLY on differences. "
                "Do NOT restate unchanged content. Be precise and brief."
            ),
            (
                "human",
                "Compare previous vs new. Output JSON using the schema.\n"
                "Previous (truncated):\n{old}\n\nNew (truncated):\n{new}\n\n"
                "Added items: {added}\nRemoved items: {removed}\n\n"
                "{format_instructions}"
            ),
        ]
    ).partial(format_instructions=parser.get_format_instructions())

    return prompt | llm | parser
