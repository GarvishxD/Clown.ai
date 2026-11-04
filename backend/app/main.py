

import os

import uuid

import mimetypes



from datetime import datetime, timezone

from pathlib import Path



from dotenv import load_dotenv



from fastapi import (

    FastAPI,

    Depends,

    HTTPException,

    Query,

    Request,

    UploadFile,

    File,

    Form,

)

from fastapi.middleware.cors import CORSMiddleware

from fastapi.responses import JSONResponse, FileResponse



from sqlalchemy import select, func

from sqlalchemy.orm import Session



from .database import Base, engine, get_db

from .models import Conversation, Message, Expense, Task, UploadedFile

from .schemas import *

from .services.assistant import chat, generate_conversation_title





# --------------------------------------------------

# ENVIRONMENT CONFIGURATION

# --------------------------------------------------



load_dotenv(

    Path(__file__).resolve().parents[1] / ".env",

    override=True

)





# --------------------------------------------------

# DATABASE INITIALIZATION

# --------------------------------------------------



Base.metadata.create_all(bind=engine)





# --------------------------------------------------

# FASTAPI APPLICATION

# --------------------------------------------------



app = FastAPI(

    title="Clown.ai API",

    version="1.0.0"

)





# --------------------------------------------------

# CORS CONFIGURATION

# Supports local development and deployed Vercel frontend

# --------------------------------------------------



frontend_origin = os.getenv("FRONTEND_ORIGIN", "").strip().rstrip("/")

allowed_origins = [
    "https://clown-ai-az11.vercel.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

if frontend_origin and frontend_origin not in allowed_origins:
    allowed_origins.append(frontend_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


UPLOAD_DIR = Path(

    os.getenv(

        "UPLOAD_DIR",

        str(Path(__file__).resolve().parents[1] / "uploads")

    )

).resolve()



MAX_UPLOAD_BYTES = (

    int(os.getenv("MAX_UPLOAD_SIZE_MB", "20")) * 1024 * 1024

)



ALLOWED_EXTENSIONS = {

    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",

    ".pdf", ".doc", ".docx", ".txt", ".rtf", ".md",

    ".xls", ".xlsx", ".csv", ".ppt", ".pptx",

    ".py", ".java", ".js", ".jsx", ".ts", ".tsx",

    ".html", ".css", ".json", ".sql",

    ".c", ".cpp", ".cs", ".php", ".go", ".rs",

    ".mp3", ".wav", ".m4a", ".ogg", ".webm",

    ".mp4", ".mov", ".avi",

}





# --------------------------------------------------

# CONVERSATION TITLE GENERATION

# --------------------------------------------------



def conversation_title(message: str) -> str:

    """Make a useful first-message title without spending another model request."""



    words = [

        word.strip(".,!?;:()[]{}\"'`…")

        for word in message.split()

    ]



    words = [word for word in words if word]



    title = " ".join(

        word[:1].upper() + word[1:]

        for word in words[:6]

    )



    return title[:65] or "New conversation"





# --------------------------------------------------

# GLOBAL EXCEPTION HANDLER

# --------------------------------------------------



@app.exception_handler(Exception)

async def unexpected_error(request: Request, exc: Exception):

    print(

        f"Unhandled API error at {request.url.path}: "

        f"{type(exc).__name__}: {exc}"

    )



    return JSONResponse(

        status_code=500,

        content={

            "detail": "Something went wrong on the server. Check the backend logs."

        },

    )





# --------------------------------------------------

# ROOT ENDPOINTS

# --------------------------------------------------



@app.get("/")

@app.get("/api")

def api_root():

    """Provide a useful response when opening the deployed API domain directly."""



    return {

        "ok": True,

        "service": "Clown.ai API",

        "health": "/api/health",

        "docs": "/docs",

    }





# --------------------------------------------------

# HEALTH CHECK

# --------------------------------------------------



@app.get("/api/health")

def health():

    return {

        "ok": True,

        "clown_ready": bool(os.getenv("GEMINI_API_KEY")),

    }





# --------------------------------------------------

# CREATE CONVERSATION

# --------------------------------------------------



@app.post(

    "/api/conversations",

    response_model=ConversationOut

)

def create_conversation(

    db: Session = Depends(get_db)

):

    conversation = Conversation()



    db.add(conversation)

    db.commit()

    db.refresh(conversation)



    return conversation





# --------------------------------------------------

# GET ALL CONVERSATIONS

# --------------------------------------------------



@app.get(

    "/api/conversations",

    response_model=list[ConversationOut]

)

def conversations(

    search: str | None = None,

    db: Session = Depends(get_db)

):

    query = select(Conversation).order_by(

        Conversation.updated_at.desc()

    )



    if search:

        query = (

            query.join(Message)

            .where(Message.content.ilike(f"%{search}%"))

            .distinct()

        )



    return db.scalars(query).all()





# --------------------------------------------------

# GET CONVERSATION MESSAGES

# --------------------------------------------------



@app.get(

    "/api/conversations/{cid}/messages",

    response_model=list[MessageOut]

)

def messages(

    cid: int,

    db: Session = Depends(get_db)

):

    conversation = db.get(Conversation, cid)



    if not conversation:

        raise HTTPException(

            status_code=404,

            detail="Conversation not found"

        )



    rows = db.scalars(

        select(Message)

        .where(Message.conversation_id == cid)

        .order_by(Message.timestamp)

    ).all()



    return [

        {

            **{

                key: getattr(message, key)

                for key in (

                    "id",

                    "conversation_id",

                    "role",

                    "content",

                    "timestamp",

                    "intent",

                )

            },

            "attachments": [

                file_json(file)

                for file in message.attachments

            ],

        }

        for message in rows

    ]





# --------------------------------------------------

# FILE RESPONSE FORMAT

# --------------------------------------------------



def file_json(file: UploadedFile):

    return {

        "id": file.id,

        "original_filename": file.original_filename,

        "mime_type": file.mime_type,

        "file_size": file.file_size,

        "status": "uploaded",

        "url": f"/api/files/{file.id}",

    }





# --------------------------------------------------

# UPLOAD FILE

# --------------------------------------------------



@app.post("/api/files/upload")

async def upload_file(

    file: UploadFile = File(...),

    conversation_id: int | None = Form(default=None),

    db: Session = Depends(get_db),

):

    original = (

        Path(file.filename or "upload")

        .name.replace("\\", "_")

        .replace("/", "_")

        .strip()[:255]

    )



    extension = Path(original).suffix.lower()



    if extension not in ALLOWED_EXTENSIONS:

        raise HTTPException(

            status_code=415,

            detail="That file type is not supported.",

        )



    declared = (

        file.content_type or "application/octet-stream"

    ).lower()



    expected = mimetypes.guess_type(original)[0]



    # Browsers often report octet-stream.

    # Otherwise, reject obvious type mismatches.



    aliases = {

        "application/octet-stream",

        "application/zip",

        "application/vnd.ms-excel",

        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",

    }



    if extension in {

        ".txt", ".md", ".csv", ".rtf",

        ".py", ".java", ".js", ".jsx",

        ".ts", ".tsx", ".html", ".css",

        ".json", ".sql", ".c", ".cpp",

        ".cs", ".php", ".go", ".rs",

    }:

        aliases.add("text/plain")



    if extension == ".csv":

        aliases.add("text/csv")



    if extension == ".webm":

        aliases.update({

            "audio/webm",

            "video/webm",

        })



    if declared not in aliases | {expected}:

        raise HTTPException(

            status_code=415,

            detail="The uploaded file type does not match its extension.",

        )



    data = await file.read(MAX_UPLOAD_BYTES + 1)



    if not data:

        raise HTTPException(

            status_code=400,

            detail="The file is empty.",

        )



    if len(data) > MAX_UPLOAD_BYTES:

        raise HTTPException(

            status_code=413,

            detail=(

                f"Files must be "

                f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB or smaller."

            ),

        )



    if extension == ".pdf" and not data.startswith(b"%PDF-"):

        raise HTTPException(

            status_code=415,

            detail="This file does not appear to be a valid PDF.",

        )



    conversation = (

        db.get(Conversation, conversation_id)

        if conversation_id

        else None

    )



    if conversation_id and not conversation:

        raise HTTPException(

            status_code=404,

            detail="Conversation not found.",

        )



    if not conversation:

        conversation = Conversation()



        db.add(conversation)

        db.commit()

        db.refresh(conversation)



    stored_filename = f"{uuid.uuid4().hex}{extension}"



    UPLOAD_DIR.mkdir(

        parents=True,

        exist_ok=True

    )



    target = (UPLOAD_DIR / stored_filename).resolve()



    if target.parent != UPLOAD_DIR:

        raise HTTPException(

            status_code=400,

            detail="Invalid file path.",

        )



    target.write_bytes(data)



    mime = (

        declared

        if extension == ".webm"

        and declared in {"audio/webm", "video/webm"}

        else expected or declared

    )



    item = UploadedFile(

        conversation_id=conversation.id,

        original_filename=original,

        stored_filename=stored_filename,

        file_path=str(target),

        mime_type=mime,

        file_size=len(data),

    )



    db.add(item)

    db.commit()

    db.refresh(item)



    return {

        **file_json(item),

        "conversation_id": conversation.id,

    }





# --------------------------------------------------

# DOWNLOAD / VIEW FILE

# --------------------------------------------------



@app.get("/api/files/{file_id}")

def get_file(

    file_id: int,

    db: Session = Depends(get_db)

):

    item = db.get(UploadedFile, file_id)



    if not item or not Path(item.file_path).is_file():

        raise HTTPException(

            status_code=404,

            detail="File not found.",

        )



    return FileResponse(

        item.file_path,

        media_type=item.mime_type,

        filename=item.original_filename,

        content_disposition_type=(

            "inline"

            if item.mime_type.startswith("image/")

            else "attachment"

        ),

    )





# --------------------------------------------------

# DELETE FILE

# --------------------------------------------------



@app.delete("/api/files/{file_id}")

def remove_file(

    file_id: int,

    db: Session = Depends(get_db)

):

    item = db.get(UploadedFile, file_id)



    if not item:

        return {"ok": True}



    try:

        Path(item.file_path).unlink(missing_ok=True)

    except OSError:

        pass



    db.delete(item)

    db.commit()



    return {"ok": True}





# --------------------------------------------------

# RENAME CONVERSATION

# --------------------------------------------------



@app.patch(

    "/api/conversations/{cid}",

    response_model=ConversationOut

)

def rename_conversation(

    cid: int,

    payload: dict,

    db: Session = Depends(get_db)

):

    conversation = db.get(Conversation, cid)



    if not conversation:

        raise HTTPException(

            status_code=404,

            detail="Conversation not found",

        )



    conversation.title = str(

        payload.get("title", conversation.title)

    )[:180]



    db.commit()

    db.refresh(conversation)



    return conversation





# --------------------------------------------------

# DELETE CONVERSATION

# --------------------------------------------------



@app.delete("/api/conversations/{cid}")

def delete_conversation(

    cid: int,

    db: Session = Depends(get_db)

):

    conversation = db.get(Conversation, cid)



    if not conversation:

        raise HTTPException(

            status_code=404,

            detail="Conversation not found",

        )



    stored_files = db.scalars(

        select(UploadedFile)

        .where(UploadedFile.conversation_id == cid)

    ).all()



    for item in stored_files:

        try:

            Path(item.file_path).unlink(missing_ok=True)

        except OSError:

            pass



        db.delete(item)



    db.delete(conversation)

    db.commit()



    return {"ok": True}





# --------------------------------------------------

# AI ASSISTANT CHAT

# --------------------------------------------------



@app.post(

    "/api/assistant/chat",

    response_model=ChatOut

)

def assistant_chat(

    payload: ChatIn,

    db: Session = Depends(get_db)

):

    if not payload.message.strip() and not payload.file_ids:

        raise HTTPException(

            status_code=422,

            detail="Enter a message or attach a file.",

        )



    conversation = (

        db.get(Conversation, payload.conversation_id)

        if payload.conversation_id

        else None

    )



    if payload.conversation_id and not conversation:

        raise HTTPException(

            status_code=404,

            detail="Conversation not found",

        )



    if not conversation:

        conversation = Conversation()



        db.add(conversation)

        db.commit()

        db.refresh(conversation)



    files: list[UploadedFile] = (

        list(

            db.scalars(

                select(UploadedFile).where(

                    UploadedFile.id.in_(payload.file_ids),

                    UploadedFile.conversation_id == conversation.id,

                )

            ).all()

        )

        if payload.file_ids

        else []

    )



    if len(files) != len(set(payload.file_ids)):

        raise HTTPException(

            status_code=400,

            detail="One or more attachments are not available in this conversation.",

        )



    content = (

        payload.message.strip()

        or "Please analyze the attached file(s) and summarize what you find."

    )



    user_message = Message(

        conversation_id=conversation.id,

        role="user",

        content=content,

    )



    db.add(user_message)

    db.flush()



    for file in files:

        file.message_id = user_message.id



    db.commit()



    first_message = conversation.title == "New conversation"



    try:

        reply, intent, action = chat(

            db,

            conversation,

            content,

            files,

        )



    except Exception as exc:

        print(

            f"Attachment analysis failed: "

            f"{type(exc).__name__}: {exc}"

        )



        reply = f"I couldn't analyze the attachment: {str(exc)}"

        intent = "attachment_error"

        action = None



    if first_message:

        conversation.title = (

            generate_conversation_title(content, reply)

            or conversation_title(content)

        )



    db.add(

        Message(

            conversation_id=conversation.id,

            role="assistant",

            content=reply,

            intent=intent,

        )

    )



    conversation.updated_at = datetime.now(timezone.utc)



    db.commit()



    return {

        "conversation_id": conversation.id,

        "response": reply,

        "intent": intent,

        "action": action,

    }





# --------------------------------------------------

# GET EXPENSES

# --------------------------------------------------



@app.get(

    "/api/expenses",

    response_model=list[ExpenseOut]

)

def expenses(

    limit: int = Query(100, ge=1, le=500),

    db: Session = Depends(get_db),

):

    return db.scalars(

        select(Expense)

        .order_by(Expense.created_at.desc())

        .limit(limit)

    ).all()





# --------------------------------------------------

# ADD EXPENSE

# --------------------------------------------------



@app.post(

    "/api/expenses",

    response_model=ExpenseOut

)

def add_expense(

    data: ExpenseIn,

    db: Session = Depends(get_db)

):

    expense = Expense(**data.model_dump())



    db.add(expense)

    db.commit()

    db.refresh(expense)



    return expense





# --------------------------------------------------

# UPDATE EXPENSE

# --------------------------------------------------



@app.patch(

    "/api/expenses/{eid}",

    response_model=ExpenseOut

)

def patch_expense(

    eid: int,

    data: ExpensePatch,

    db: Session = Depends(get_db)

):

    expense = db.get(Expense, eid)



    if not expense:

        raise HTTPException(

            status_code=404,

            detail="Expense not found",

        )



    for key, value in data.model_dump(

        exclude_unset=True

    ).items():

        setattr(expense, key, value)



    db.commit()

    db.refresh(expense)



    return expense





# --------------------------------------------------

# DELETE EXPENSE

# --------------------------------------------------



@app.delete("/api/expenses/{eid}")

def remove_expense(

    eid: int,

    db: Session = Depends(get_db)

):

    expense = db.get(Expense, eid)



    if not expense:

        raise HTTPException(

            status_code=404,

            detail="Expense not found",

        )



    db.delete(expense)

    db.commit()



    return {"ok": True}





# --------------------------------------------------

# EXPENSE SUMMARY

# --------------------------------------------------



@app.get("/api/expenses/summary")

def expense_summary(

    db: Session = Depends(get_db)

):

    rows = db.scalars(

        select(Expense)

    ).all()



    now = datetime.now().astimezone()



    month_expenses = [

        expense

        for expense in rows

        if (

            expense.created_at.year == now.year

            and expense.created_at.month == now.month

        )

    ]



    categories = {}



    for expense in month_expenses:

        categories[expense.category] = (

            categories.get(expense.category, 0)

            + expense.amount

        )



    recent = [

        ExpenseOut.model_validate(expense).model_dump(mode="json")

        for expense in sorted(

            rows,

            key=lambda expense: expense.created_at,

            reverse=True,

        )[:5]

    ]



    return {

        "month_total": sum(

            expense.amount for expense in month_expenses

        ),

        "count": len(month_expenses),

        "by_category": categories,

        "recent": recent,

    }





# --------------------------------------------------

# GET TASKS

# --------------------------------------------------



@app.get(

    "/api/tasks",

    response_model=list[TaskOut]

)

def tasks(

    status: str | None = None,

    db: Session = Depends(get_db)

):

    query = select(Task).order_by(

        Task.due_date.asc().nullslast(),

        Task.created_at.desc(),

    )



    if status in ("pending", "completed"):

        query = query.where(Task.status == status)



    return db.scalars(query).all()





# --------------------------------------------------

# ADD TASK

# --------------------------------------------------



@app.post(

    "/api/tasks",

    response_model=TaskOut

)

def add_task(

    data: TaskIn,

    db: Session = Depends(get_db)

):

    task = Task(**data.model_dump())



    db.add(task)

    db.commit()

    db.refresh(task)



    return task





# --------------------------------------------------

# UPDATE TASK

# --------------------------------------------------



@app.patch(

    "/api/tasks/{tid}",

    response_model=TaskOut

)

def patch_task(

    tid: int,

    data: TaskPatch,

    db: Session = Depends(get_db)

):

    task = db.get(Task, tid)



    if not task:

        raise HTTPException(

            status_code=404,

            detail="Task not found",

        )



    for key, value in data.model_dump(

        exclude_unset=True

    ).items():

        setattr(task, key, value)



    db.commit()

    db.refresh(task)



    return task





# --------------------------------------------------

# COMPLETE TASK

# --------------------------------------------------



@app.patch(

    "/api/tasks/{tid}/complete",

    response_model=TaskOut

)

def complete_task(

    tid: int,

    db: Session = Depends(get_db)

):

    task = db.get(Task, tid)



    if not task:

        raise HTTPException(

            status_code=404,

            detail="Task not found",

        )



    task.status = "completed"



    db.commit()

    db.refresh(task)



    return task





# --------------------------------------------------

# DELETE TASK

# --------------------------------------------------



@app.delete("/api/tasks/{tid}")

def delete_task(

    tid: int,

    db: Session = Depends(get_db)

):

    task = db.get(Task, tid)



    if not task:

        raise HTTPException(

            status_code=404,

            detail="Task not found",

        )



    db.delete(task)

    db.commit()



    return {"ok": True}





# --------------------------------------------------

# DASHBOARD SUMMARY

# --------------------------------------------------



@app.get("/api/dashboard/summary")

def dashboard(

    db: Session = Depends(get_db)

):

    all_expenses = db.scalars(

        select(Expense)

    ).all()



    all_tasks = db.scalars(

        select(Task)

    ).all()



    now = datetime.now().astimezone()



    month_expenses = [

        expense

        for expense in all_expenses

        if (

            expense.created_at.year == now.year

            and expense.created_at.month == now.month

        )

    ]



    categories = {}



    for expense in month_expenses:

        categories[expense.category] = (

            categories.get(expense.category, 0)

            + expense.amount

        )



    recent_expenses = [

        ExpenseOut.model_validate(expense).model_dump(mode="json")

        for expense in sorted(

            all_expenses,

            key=lambda expense: expense.created_at,

            reverse=True,

        )[:5]

    ]



    upcoming_tasks = [

        TaskOut.model_validate(task).model_dump(mode="json")

        for task in sorted(

            [

                task

                for task in all_tasks

                if task.status == "pending"

            ],

            key=lambda task: (

                task.due_date

                or datetime.max.replace(tzinfo=timezone.utc)

            ),

        )[:5]

    ]



    return {

        "month_total": sum(

            expense.amount for expense in month_expenses

        ),

        "expense_count": len(month_expenses),

        "pending_tasks": sum(

            task.status == "pending"

            for task in all_tasks

        ),

        "completed_tasks": sum(

            task.status == "completed"

            for task in all_tasks

        ),

        "categories": categories,

        "recent_expenses": recent_expenses,

        "upcoming_tasks": upcoming_tasks,

    }
