from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app import seed
from app.modules import catalog, lending
from app.modules.lending import LendingError

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup():
    seed.init_db()


@app.get("/api/health")
def health():
    return {"ok": True, "project": "borrowboard"}


@app.get("/api/items")
def items():
    return catalog.list_items()


@app.get("/api/board")
def board():
    # One read in the commit layer -> left pane, right pane and top strip
    # all render the same snapshot.
    return lending.get_board(date.today().isoformat())


class ItemIn(BaseModel):
    title: str
    owner: str


@app.post("/api/items")
def add_item(body: ItemIn):
    return {"id": catalog.create_item(body.title, body.owner)}


class LendIn(BaseModel):
    borrower: str
    due_date: str


@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    # The single orchestration endpoint the frontend's "借出通过" calls.
    try:
        loan_id = lending.lend_item(iid, body.borrower, body.due_date)
    except LendingError as e:
        raise HTTPException(e.status_code, e.detail)
    return {"loan_id": loan_id}


@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    try:
        lending.return_loan(lid)
    except LendingError as e:
        raise HTTPException(e.status_code, e.detail)
    return {"ok": True}


@app.get("/api/loans")
def loans():
    return lending.list_loans(date.today().isoformat())


@app.get("/api/settings")
def settings():
    return catalog.get_settings()
