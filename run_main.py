import uvicorn
from app.main import app

if __name__ == "__main__":
    print("\n【提示】请在浏览器访问: http://localhost:8002/\n")
    uvicorn.run(app, host='0.0.0.0', port=8002)