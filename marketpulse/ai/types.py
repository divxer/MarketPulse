from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AnalysisResult:
    ticker: str
    model: str
    prompt_version: str
    response_markdown: str
    requested_at: datetime
    cached: bool = False
