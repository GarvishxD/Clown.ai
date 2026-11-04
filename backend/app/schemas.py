from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class ConversationOut(ORM):
    id: int; title: str; created_at: datetime; updated_at: datetime
class MessageOut(ORM):
    id: int; conversation_id: int; role: str; content: str; timestamp: datetime; intent: str | None = None
    attachments: list[dict] = []
class ExpenseIn(BaseModel):
    amount: float = Field(gt=0); currency: str = "INR"; category: str = "Other"; description: str = Field(min_length=1, max_length=240)
class ExpensePatch(BaseModel):
    amount: float | None = Field(default=None, gt=0); currency: str | None = None; category: str | None = None; description: str | None = None
class ExpenseOut(ORM):
    id: int; amount: float; currency: str; category: str; description: str; created_at: datetime
class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=240); description: str = ""; due_date: datetime | None = None
class TaskPatch(BaseModel):
    title: str | None = None; description: str | None = None; due_date: datetime | None = None; status: str | None = None
class TaskOut(ORM):
    id: int; title: str; description: str; due_date: datetime | None; status: str; created_at: datetime; updated_at: datetime
class ChatIn(BaseModel):
    message: str = Field(default="", max_length=6000); conversation_id: int | None = None; file_ids: list[int] = Field(default_factory=list, max_length=10)
class ChatOut(BaseModel):
    conversation_id: int; response: str; intent: str = "conversation"; action: dict | list[dict] | None = None
