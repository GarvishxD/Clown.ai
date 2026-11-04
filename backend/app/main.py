import os
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Query, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/".env", override=True)
from .database import Base, engine, get_db
from .models import Conversation, Message, Expense, Task, UploadedFile
from .schemas import *
from .services.assistant import chat, generate_conversation_title
from fastapi.responses import FileResponse
import uuid, mimetypes

Base.metadata.create_all(bind=engine)
app=FastAPI(title="Clown.ai API",version="1.0.0")
app.add_middleware(CORSMiddleware,allow_origins=[os.getenv("FRONTEND_ORIGIN","http://localhost:5173"),"http://127.0.0.1:5173"],allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

UPLOAD_DIR=Path(os.getenv("UPLOAD_DIR",str(Path(__file__).resolve().parents[1]/"uploads"))).resolve()
MAX_UPLOAD_BYTES=int(os.getenv("MAX_UPLOAD_SIZE_MB","20"))*1024*1024
ALLOWED_EXTENSIONS={'.png','.jpg','.jpeg','.webp','.gif','.bmp','.pdf','.doc','.docx','.txt','.rtf','.md','.xls','.xlsx','.csv','.ppt','.pptx','.py','.java','.js','.jsx','.ts','.tsx','.html','.css','.json','.sql','.c','.cpp','.cs','.php','.go','.rs','.mp3','.wav','.m4a','.ogg','.webm','.mp4','.mov','.avi'}

def conversation_title(message: str) -> str:
    """Make a useful first-message title without spending another model request."""
    words=[w.strip(".,!?;:()[]{}\"'`…") for w in message.split()]
    words=[w for w in words if w]
    title=" ".join(w[:1].upper()+w[1:] for w in words[:6])
    return title[:65] or "New conversation"

@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    print(f"Unhandled API error at {request.url.path}: {type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500,content={"detail":"Something went wrong on the server. Check the backend logs."})

@app.get("/")
@app.get("/api")
def api_root():
    """Provide a useful response when opening the deployed API domain directly."""
    return {"ok":True,"service":"Clown.ai API","health":"/api/health","docs":"/docs"}

@app.get("/api/health")
def health(): return {"ok":True,"clown_ready":bool(os.getenv("GEMINI_API_KEY"))}
@app.post("/api/conversations",response_model=ConversationOut)
def create_conversation(db:Session=Depends(get_db)):
    c=Conversation(); db.add(c); db.commit(); db.refresh(c); return c
@app.get("/api/conversations",response_model=list[ConversationOut])
def conversations(search:str|None=None,db:Session=Depends(get_db)):
    q=select(Conversation).order_by(Conversation.updated_at.desc())
    if search:q=q.join(Message).where(Message.content.ilike(f"%{search}%")).distinct()
    return db.scalars(q).all()
@app.get("/api/conversations/{cid}/messages",response_model=list[MessageOut])
def messages(cid:int,db:Session=Depends(get_db)):
    if not db.get(Conversation,cid):raise HTTPException(404,"Conversation not found")
    rows=db.scalars(select(Message).where(Message.conversation_id==cid).order_by(Message.timestamp)).all()
    return [{**{k:getattr(m,k) for k in ("id","conversation_id","role","content","timestamp","intent")},"attachments":[file_json(f) for f in m.attachments]} for m in rows]

def file_json(f:UploadedFile):
    return {"id":f.id,"original_filename":f.original_filename,"mime_type":f.mime_type,"file_size":f.file_size,"status":"uploaded","url":f"/api/files/{f.id}"}

@app.post("/api/files/upload")
async def upload_file(file:UploadFile=File(...),conversation_id:int|None=Form(default=None),db:Session=Depends(get_db)):
    original=Path(file.filename or "upload").name.replace("\\","_").replace("/","_").strip()[:255]
    ext=Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS: raise HTTPException(415,"That file type is not supported.")
    declared=(file.content_type or "application/octet-stream").lower()
    expected=mimetypes.guess_type(original)[0]
    # Browsers often report octet-stream; otherwise reject obvious type mismatches.
    aliases={"application/octet-stream","application/zip","application/vnd.ms-excel","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    if ext in {".txt",".md",".csv",".rtf",".py",".java",".js",".jsx",".ts",".tsx",".html",".css",".json",".sql",".c",".cpp",".cs",".php",".go",".rs"}: aliases.add("text/plain")
    if ext==".csv": aliases.add("text/csv")
    if ext==".webm": aliases.update({"audio/webm","video/webm"})
    if declared not in aliases|{expected}:
        raise HTTPException(415,"The uploaded file type does not match its extension.")
    data=await file.read(MAX_UPLOAD_BYTES+1)
    if not data: raise HTTPException(400,"The file is empty.")
    if len(data)>MAX_UPLOAD_BYTES: raise HTTPException(413,f"Files must be {MAX_UPLOAD_BYTES//(1024*1024)} MB or smaller.")
    if ext=='.pdf' and not data.startswith(b'%PDF-'): raise HTTPException(415,"This file does not appear to be a valid PDF.")
    c=db.get(Conversation,conversation_id) if conversation_id else None
    if conversation_id and not c: raise HTTPException(404,"Conversation not found.")
    if not c: c=Conversation(); db.add(c); db.commit(); db.refresh(c)
    stored=f"{uuid.uuid4().hex}{ext}"
    UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
    target=(UPLOAD_DIR/stored).resolve()
    if target.parent!=UPLOAD_DIR: raise HTTPException(400,"Invalid file path.")
    target.write_bytes(data)
    mime=declared if ext==".webm" and declared in {"audio/webm","video/webm"} else expected or declared
    item=UploadedFile(conversation_id=c.id,original_filename=original,stored_filename=stored,file_path=str(target),mime_type=mime,file_size=len(data))
    db.add(item); db.commit(); db.refresh(item)
    return {**file_json(item),"conversation_id":c.id}

@app.get("/api/files/{file_id}")
def get_file(file_id:int,db:Session=Depends(get_db)):
    item=db.get(UploadedFile,file_id)
    if not item or not Path(item.file_path).is_file():raise HTTPException(404,"File not found.")
    return FileResponse(item.file_path,media_type=item.mime_type,filename=item.original_filename,content_disposition_type="inline" if item.mime_type.startswith("image/") else "attachment")

@app.delete("/api/files/{file_id}")
def remove_file(file_id:int,db:Session=Depends(get_db)):
    item=db.get(UploadedFile,file_id)
    if not item: return {"ok":True}
    try: Path(item.file_path).unlink(missing_ok=True)
    except OSError: pass
    db.delete(item);db.commit();return {"ok":True}
@app.patch("/api/conversations/{cid}",response_model=ConversationOut)
def rename_conversation(cid:int,payload:dict,db:Session=Depends(get_db)):
    c=db.get(Conversation,cid)
    if not c:raise HTTPException(404,"Conversation not found")
    c.title=str(payload.get("title",c.title))[:180]; db.commit(); db.refresh(c); return c
@app.delete("/api/conversations/{cid}")
def delete_conversation(cid:int,db:Session=Depends(get_db)):
    c=db.get(Conversation,cid)
    if not c:raise HTTPException(404,"Conversation not found")
    stored=db.scalars(select(UploadedFile).where(UploadedFile.conversation_id==cid)).all()
    for item in stored:
        try:Path(item.file_path).unlink(missing_ok=True)
        except OSError:pass
        db.delete(item)
    db.delete(c); db.commit(); return {"ok":True}
@app.post("/api/assistant/chat",response_model=ChatOut)
def assistant_chat(payload:ChatIn,db:Session=Depends(get_db)):
    if not payload.message.strip() and not payload.file_ids: raise HTTPException(422,"Enter a message or attach a file.")
    c=db.get(Conversation,payload.conversation_id) if payload.conversation_id else None
    if payload.conversation_id and not c:raise HTTPException(404,"Conversation not found")
    if not c:c=Conversation(); db.add(c); db.commit(); db.refresh(c)
    files: list[UploadedFile]=list(db.scalars(select(UploadedFile).where(UploadedFile.id.in_(payload.file_ids),UploadedFile.conversation_id==c.id)).all()) if payload.file_ids else []
    if len(files)!=len(set(payload.file_ids)): raise HTTPException(400,"One or more attachments are not available in this conversation.")
    content=payload.message.strip() or "Please analyze the attached file(s) and summarize what you find."
    user=Message(conversation_id=c.id,role="user",content=content); db.add(user); db.flush()
    for f in files:f.message_id=user.id
    db.commit()
    first_message=c.title=="New conversation"
    try: reply,intent,action=chat(db,c,content,files)
    except Exception as exc:
        print(f"Attachment analysis failed: {type(exc).__name__}: {exc}")
        reply=f"I couldn't analyze the attachment: {str(exc)}";intent="attachment_error";action=None
    if first_message:
        c.title=generate_conversation_title(content,reply) or conversation_title(content)
    db.add(Message(conversation_id=c.id,role="assistant",content=reply,intent=intent)); c.updated_at=datetime.now(timezone.utc); db.commit()
    return {"conversation_id":c.id,"response":reply,"intent":intent,"action":action}

@app.get("/api/expenses",response_model=list[ExpenseOut])
def expenses(limit:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):return db.scalars(select(Expense).order_by(Expense.created_at.desc()).limit(limit)).all()
@app.post("/api/expenses",response_model=ExpenseOut)
def add_expense(data:ExpenseIn,db:Session=Depends(get_db)):
    x=Expense(**data.model_dump()); db.add(x); db.commit(); db.refresh(x); return x
@app.patch("/api/expenses/{eid}",response_model=ExpenseOut)
def patch_expense(eid:int,data:ExpensePatch,db:Session=Depends(get_db)):
    x=db.get(Expense,eid)
    if not x:raise HTTPException(404,"Expense not found")
    for k,v in data.model_dump(exclude_unset=True).items():setattr(x,k,v)
    db.commit(); db.refresh(x); return x
@app.delete("/api/expenses/{eid}")
def remove_expense(eid:int,db:Session=Depends(get_db)):
    x=db.get(Expense,eid)
    if not x:raise HTTPException(404,"Expense not found")
    db.delete(x); db.commit(); return {"ok":True}
@app.get("/api/expenses/summary")
def expense_summary(db:Session=Depends(get_db)):
    rows=db.scalars(select(Expense)).all(); now=datetime.now().astimezone(); month=[x for x in rows if x.created_at.year==now.year and x.created_at.month==now.month]
    cats={}
    for x in month:cats[x.category]=cats.get(x.category,0)+x.amount
    return {"month_total":sum(x.amount for x in month),"count":len(month),"by_category":cats,"recent": [ExpenseOut.model_validate(x).model_dump(mode="json") for x in sorted(rows,key=lambda x:x.created_at,reverse=True)[:5]]}
@app.get("/api/tasks",response_model=list[TaskOut])
def tasks(status:str|None=None,db:Session=Depends(get_db)):
    q=select(Task).order_by(Task.due_date.asc().nullslast(),Task.created_at.desc())
    if status in ("pending","completed"):q=q.where(Task.status==status)
    return db.scalars(q).all()
@app.post("/api/tasks",response_model=TaskOut)
def add_task(data:TaskIn,db:Session=Depends(get_db)):
    x=Task(**data.model_dump()); db.add(x); db.commit(); db.refresh(x); return x
@app.patch("/api/tasks/{tid}",response_model=TaskOut)
def patch_task(tid:int,data:TaskPatch,db:Session=Depends(get_db)):
    x=db.get(Task,tid)
    if not x:raise HTTPException(404,"Task not found")
    for k,v in data.model_dump(exclude_unset=True).items():setattr(x,k,v)
    db.commit(); db.refresh(x); return x
@app.patch("/api/tasks/{tid}/complete",response_model=TaskOut)
def complete_task(tid:int,db:Session=Depends(get_db)):
    x=db.get(Task,tid)
    if not x:raise HTTPException(404,"Task not found")
    x.status="completed"; db.commit(); db.refresh(x); return x
@app.delete("/api/tasks/{tid}")
def delete_task(tid:int,db:Session=Depends(get_db)):
    x=db.get(Task,tid)
    if not x:raise HTTPException(404,"Task not found")
    db.delete(x); db.commit(); return {"ok":True}
@app.get("/api/dashboard/summary")
def dashboard(db:Session=Depends(get_db)):
    es=db.scalars(select(Expense)).all(); ts=db.scalars(select(Task)).all(); now=datetime.now().astimezone(); month=[e for e in es if e.created_at.year==now.year and e.created_at.month==now.month]
    by={}
    for e in month:by[e.category]=by.get(e.category,0)+e.amount
    return {"month_total":sum(e.amount for e in month),"expense_count":len(month),"pending_tasks":sum(t.status=="pending" for t in ts),"completed_tasks":sum(t.status=="completed" for t in ts),"categories":by,"recent_expenses":[ExpenseOut.model_validate(e).model_dump(mode="json") for e in sorted(es,key=lambda x:x.created_at,reverse=True)[:5]],"upcoming_tasks":[TaskOut.model_validate(t).model_dump(mode="json") for t in sorted([x for x in ts if x.status=="pending"],key=lambda x:x.due_date or datetime.max.replace(tzinfo=timezone.utc))[:5]]}
