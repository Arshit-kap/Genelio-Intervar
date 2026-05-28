import sys, os
sys.path.insert(0, "/home/ubuntu/intervar")
os.chdir("/home/ubuntu/intervar")

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.ai.intervar_router import route_and_execute, build_answer_user_message, render_executor
from app.ai.llm_config import get_llm

engine = create_engine("sqlite:///patient_variants.db")
Session = sessionmaker(bind=engine)
db = Session()

message = "What is the variant at chromosome 1 position 17270928"
profile = {}
history = []

print("=== STEP 1: route_and_execute ===")
decision, executor, resolution = route_and_execute(message, db, profile, history)
print(f"Intent: {decision.intent}")
print(f"Chr: {decision.chr}, Start: {decision.start}")
print(f"Executor kind: {executor.kind}")
print(f"Executor rows count: {len(executor.rows)}")
print(f"Executor total_matched: {executor.total_matched}")

print("\n=== STEP 2: render_executor output (sent to MedGemma) ===")
rendered = render_executor(executor)
print(repr(rendered[:500]))

print("\n=== STEP 3: build_answer_user_message ===")
user_msg = build_answer_user_message(message, executor, profile, decision, resolution)
print(repr(user_msg[:800]))

print("\n=== STEP 4: call llm.answer() ===")
llm = get_llm()
print(f"LLM type: {type(llm).__name__}")
if llm and hasattr(llm, "answer"):
    answer = llm.answer(user_msg, history=[])
    print(f"Answer: {repr(answer[:500])}")
else:
    print("No answer() method!")

db.close()
