from pydantic import BaseModel


class Tick(BaseModel):
    instrument: str
    price: float
    volume: float
    timestamp: str  # ISO 8601
