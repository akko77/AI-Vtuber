from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.post("/heartbeat")
async def heartbeat():
    return {"status": "alive", "service": "FastAPI"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)