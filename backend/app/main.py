from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.services import lending
from app.services.lending import LendRejected

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    return lending.list_items()

@app.get("/api/board")
def board():
    # Strip counts and both panes come from the same service snapshot.
    return lending.get_board()

class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    return {"id": lending.add_item(body.title, body.owner)}

class LendIn(BaseModel):
    borrower: str
    due_date: str

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    # Route only wires HTTP to the commit layer; rules live in engines.
    try:
        loan_id = lending.lend_item(iid, body.borrower, body.due_date)
    except LendRejected as e:
        raise HTTPException(e.status_code, e.reason)
    return {"loan_id": loan_id}

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    try:
        lending.return_loan(lid)
    except LendRejected as e:
        raise HTTPException(e.status_code, e.reason)
    return {"ok": True}

@app.get("/api/loans")
def loans():
    return lending.list_loans()

@app.get("/api/settings")
def settings():
    return lending.settings()
