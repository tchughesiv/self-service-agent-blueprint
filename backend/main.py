from fastapi import FastAPI
from agent_manager.agent_manager import AgentManager

app = FastAPI()
service = AgentManager()

@app.get("/agents")
def agents():
    return service.agents()

@app.get("/connect")
def is_connected():
    return service.is_connected()

@app.get("/config")
def config():
    return service.config()

@app.post("/agents")
def create_agents():
    return service.create_agents()

@app.post("/connect")
def connect_to_llama_stack():
    return service.connect_to_llama_stack()