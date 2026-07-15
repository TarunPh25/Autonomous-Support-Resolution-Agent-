import os
import sys
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load environmental variables
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

from agent.agent_loop import resolve_ticket
from agent.tool_registry import create_default_registry
from agent.decision_engine import DecisionEngine
from utils.llm_client import init_llm_client

# Clear the refund caches from tools for clean simulation resets
import tools.refund as refund_tool

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s"
)
logger = logging.getLogger("agent.server")

app = FastAPI(
    title="Autonomous Support Resolution Agent API",
    description="Backend API powering the Support Resolution Agent Dashboard",
    version="1.0.0"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create registry and engines
registry = create_default_registry()

# Cache active configurations
class ServerConfig:
    def __init__(self):
        self.mode = os.environ.get("AGENT_MODE", "deterministic")
        self._engine_det = DecisionEngine(use_llm=False)
        self._engine_llm = None
        self.llm_initialized = False
        
        # If API key is already in env, try to pre-init LLM
        if os.environ.get("GROQ_API_KEY"):
            self.init_llm()

    def init_llm(self):
        try:
            llm_client = init_llm_client()
            if llm_client.available:
                self._engine_llm = DecisionEngine(use_llm=True)
                self.llm_initialized = True
                logger.info("LLM engine initialized successfully.")
            else:
                self.llm_initialized = False
                logger.warning("LLM client not available. Groq key may be invalid/missing.")
        except Exception as e:
            self.llm_initialized = False
            logger.error(f"Error initializing LLM client: {e}")

    @property
    def engine(self):
        if self.mode == "llm" and self.llm_initialized and self._engine_llm:
            return self._engine_llm
        return self._engine_det

config = ServerConfig()

# Pydantic Schemas for API Requests
class TicketInput(BaseModel):
    customer_email: str
    subject: str
    body: str
    source: str = "email"
    tier: int = 1
    expected_action: Optional[str] = ""

class SettingsInput(BaseModel):
    mode: str
    groq_api_key: Optional[str] = None

# Paths
TICKETS_FILE = "tickets.json"
CUSTOMERS_FILE = "customers.json"
PRODUCTS_FILE = "products.json"
DATA_DIR = "data"
AUDIT_LOG_DIR = "output/audit_logs"

os.makedirs(AUDIT_LOG_DIR, exist_ok=True)

# Helper function to read json files safely
def read_json_file(filepath: str) -> List[Any]:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading {filepath}: {e}")
        return []

# Helper function to write json files safely
def write_json_file(filepath: str, data: Any):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error writing {filepath}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to write to file: {e}")

# Helper to merge ticket with audit log status
def get_ticket_status_and_audit(ticket: dict) -> dict:
    ticket_id = ticket.get("ticket_id")
    audit_file = os.path.join(AUDIT_LOG_DIR, f"{ticket_id}.json")
    
    merged = ticket.copy()
    merged["status"] = "pending"
    merged["confidence"] = None
    merged["steps_count"] = 0
    merged["processing_time_ms"] = 0
    merged["final_resolution"] = ""
    merged["category"] = "unclassified"
    merged["audit_log"] = None

    if os.path.exists(audit_file):
        try:
            with open(audit_file, "r", encoding="utf-8") as f:
                audit_data = json.load(f)
                summary = audit_data.get("processing_summary", {})
                merged["status"] = summary.get("final_status", "resolved")
                merged["confidence"] = summary.get("confidence", 0.0)
                merged["steps_count"] = summary.get("total_steps", 0)
                merged["processing_time_ms"] = summary.get("total_duration_ms", 0.0)
                merged["final_resolution"] = audit_data.get("resolution_message", "")
                merged["category"] = audit_data.get("category", "")
                merged["audit_log"] = audit_data
        except Exception as e:
            logger.error(f"Error parsing audit log for {ticket_id}: {e}")
            
    return merged

# ── API ENDPOINTS ──

@app.get("/api/settings")
async def get_settings():
    return {
        "mode": config.mode,
        "llm_available": config.llm_initialized,
        "has_key": bool(os.environ.get("GROQ_API_KEY"))
    }

@app.post("/api/settings")
async def update_settings(settings: SettingsInput):
    if settings.mode not in ["deterministic", "llm"]:
        raise HTTPException(status_code=400, detail="Invalid mode. Must be 'deterministic' or 'llm'.")
    
    if settings.groq_api_key:
        # Save key to environment and update .env
        os.environ["GROQ_API_KEY"] = settings.groq_api_key
        # Try to write to .env
        try:
            env_lines = []
            env_path = ".env"
            key_exists = False
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("GROQ_API_KEY="):
                            env_lines.append(f"GROQ_API_KEY={settings.groq_api_key}\n")
                            key_exists = True
                        else:
                            env_lines.append(line)
            if not key_exists:
                env_lines.append(f"GROQ_API_KEY={settings.groq_api_key}\n")
            
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(env_lines)
        except Exception as e:
            logger.error(f"Failed to write to .env: {e}")
            
        config.init_llm()
    
    if settings.mode == "llm" and not config.llm_initialized:
        # Retry initialization just in case key was set elsewhere
        config.init_llm()
        if not config.llm_initialized:
            raise HTTPException(
                status_code=400, 
                detail="Cannot switch to LLM mode: GROQ_API_KEY is not configured or invalid."
            )
            
    config.mode = settings.mode
    return {"status": "success", "mode": config.mode, "llm_available": config.llm_initialized}

@app.get("/api/tickets")
async def get_tickets():
    tickets = read_json_file(TICKETS_FILE)
    return [get_ticket_status_and_audit(t) for t in tickets]

@app.post("/api/tickets")
async def create_ticket(ticket_in: TicketInput):
    tickets = read_json_file(TICKETS_FILE)
    
    # Generate new ID
    ticket_ids = []
    for t in tickets:
        tid = t.get("ticket_id", "")
        if tid.startswith("TKT-"):
            try:
                ticket_ids.append(int(tid.split("-")[1]))
            except ValueError:
                pass
    
    next_id = max(ticket_ids) + 1 if ticket_ids else 1
    new_ticket_id = f"TKT-{next_id:03d}"
    
    new_ticket = {
        "ticket_id": new_ticket_id,
        "customer_email": ticket_in.customer_email,
        "subject": ticket_in.subject,
        "body": ticket_in.body,
        "source": ticket_in.source,
        "created_at": datetime.now().isoformat() + "Z",
        "tier": ticket_in.tier,
        "expected_action": ticket_in.expected_action or ""
    }
    
    tickets.append(new_ticket)
    write_json_file(TICKETS_FILE, tickets)
    return get_ticket_status_and_audit(new_ticket)

@app.get("/api/ticket/{ticket_id}")
async def get_ticket_details(ticket_id: str):
    tickets = read_json_file(TICKETS_FILE)
    ticket = next((t for t in tickets if t.get("ticket_id") == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return get_ticket_status_and_audit(ticket)

@app.post("/api/process/{ticket_id}")
async def process_single_ticket(ticket_id: str):
    tickets = read_json_file(TICKETS_FILE)
    ticket = next((t for t in tickets if t.get("ticket_id") == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
        
    try:
        # Run resolution in the backend loop
        audit_log = await resolve_ticket(ticket, registry, config.engine, AUDIT_LOG_DIR)
        return audit_log
    except Exception as e:
        logger.error(f"Error processing ticket {ticket_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/process_all")
async def process_all_tickets(background_tasks: BackgroundTasks):
    tickets = read_json_file(TICKETS_FILE)
    if not tickets:
        return {"status": "error", "message": "No tickets found to process."}

    # Internal runner function to process all tickets concurrently with a semaphore
    async def run_batch():
        semaphore = asyncio.Semaphore(5)
        
        async def process_with_sem(ticket):
            async with semaphore:
                try:
                    await resolve_ticket(ticket, registry, config.engine, AUDIT_LOG_DIR)
                except Exception as e:
                    logger.error(f"Batch failed on {ticket.get('ticket_id')}: {e}")

        tasks = [process_with_sem(t) for t in tickets]
        await asyncio.gather(*tasks)
        
        # Save the combined audit log just like CLI
        audit_logs = []
        for t in tickets:
            t_id = t.get("ticket_id")
            path = os.path.join(AUDIT_LOG_DIR, f"{t_id}.json")
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        audit_logs.append(json.load(f))
                except Exception:
                    pass
        
        combined_path = os.path.join("output", "combined_audit_log.json")
        try:
            with open(combined_path, "w", encoding="utf-8") as f:
                json.dump(audit_logs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to write combined audit logs: {e}")

    background_tasks.add_task(run_batch)
    return {"status": "processing", "message": f"Processing {len(tickets)} tickets in background."}

@app.get("/api/database")
async def get_database():
    customers_profiles = read_json_file(os.path.join(DATA_DIR, "customer_profiles.json"))
    kb_articles = read_json_file(os.path.join(DATA_DIR, "knowledge_base.json"))
    orders = read_json_file(CUSTOMERS_FILE)
    products = read_json_file(PRODUCTS_FILE)
    
    return {
        "customer_profiles": customers_profiles,
        "knowledge_base": kb_articles,
        "orders": orders,
        "products": products
    }

@app.post("/api/reset")
async def reset_agent_logs():
    # 1. Clear audit logs
    if os.path.exists(AUDIT_LOG_DIR):
        for f in os.listdir(AUDIT_LOG_DIR):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(AUDIT_LOG_DIR, f))
                except Exception as e:
                    logger.error(f"Failed to delete {f}: {e}")
                    
    combined_log = os.path.join("output", "combined_audit_log.json")
    if os.path.exists(combined_log):
        try:
            os.remove(combined_log)
        except Exception:
            pass

    # 2. Reset the in-memory refund states so customers can be checked/refunded again
    refund_tool._eligibility_checked.clear()
    refund_tool._refunds_issued.clear()
    
    logger.info("Simulation states and audit logs have been reset.")
    return {"status": "success", "message": "Simulation logs and transaction state reset successfully."}

# Serve static frontend files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
