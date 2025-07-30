from fastapi import FastAPI
from agent_manager.agent_manager import AgentManager

app = FastAPI()
service = AgentManager("Mike")

@app.get("/")
def main():
    return "Hello " + service.name()


if __name__ == "__main__":
    main()
