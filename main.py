import os
import subprocess
import threading
import httpx
import uvicorn
from fastapi import Query

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Terraform Executor")

TF_DIR = os.getenv("TF_DIR", "/app/tf")
API_KEY = os.getenv("EXECUTOR_API_KEY", "")
ALLOWED = {"init", "plan", "apply", "destroy", "output", "validate"}

# 防止并发执行同一 state
_lock = threading.Lock()


# ---------- IMDS 兼容层：IMDS 协议 -> Container Apps 身份端点 ----------
imds = FastAPI()


@imds.get("/metadata/identity/oauth2/token")
async def imds_token(resource: str = Query(...)):
    endpoint = os.environ.get("IDENTITY_ENDPOINT")
    header = os.environ.get("IDENTITY_HEADER")
    if not endpoint or not header:
        raise HTTPException(status_code=500, detail="identity endpoint not available")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            endpoint,
            params={"api-version": "2019-08-01", "resource": resource},
            headers={"X-IDENTITY-HEADER": header},
        )
        resp.raise_for_status()
        return resp.json()


def _start_imds_shim():
    # 只绑定 127.0.0.1，外部流量（ingress）永远到不了这里，安全
    uvicorn.run(imds, host="127.0.0.1", port=4567, log_level="error")


threading.Thread(target=_start_imds_shim, daemon=True).start()

IMDS_SHIM_URL = "http://127.0.0.1:4567/metadata/identity/oauth2/token"

def terraform_env() -> dict:
    env = os.environ.copy()
    if env.get("IDENTITY_ENDPOINT"):
        env["MSI_ENDPOINT"] = IMDS_SHIM_URL
        env["ARM_MSI_ENDPOINT"] = IMDS_SHIM_URL
        env["MSI_SECRET"] = env.get("IDENTITY_HEADER", "")
    return env

def run_terraform(command: str, extra_args: list[str]) -> dict:
    if command not in ALLOWED:
        raise HTTPException(status_code=400, detail=f"不支持的命令: {command}")

    args = ["terraform", command, "-no-color"] + extra_args
    if command in ("apply", "destroy"):
        args.append("-auto-approve")

    proc = subprocess.run(
        args,
        cwd=TF_DIR,
        capture_output=True,
        text=True,
        timeout=1800,  # 30 分钟上限
        env=terraform_env()
    )
    return {
        "command": " ".join(args),
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-20000:],  # 截断防止响应过大
        "stderr": proc.stderr[-20000:],
        "success": proc.returncode == 0,
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/terraform/{command}")
def execute(command: str, x_api_key: str = Header(default="")):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")
    if not _lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有 terraform 任务在执行中")
    try:
        # 容器文件系统是临时的，.terraform 丢失时自动重新初始化
        if command != "init" and not os.path.isdir(os.path.join(TF_DIR, ".terraform")):
            init_result = run_terraform("init", ["-input=false"])
            if not init_result["success"]:
                raise HTTPException(status_code=500, detail={"stage": "init", **init_result})

        extra = ["-input=false"] if command in ("init", "plan", "apply", "destroy") else []
        result = run_terraform(command, extra)
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result)
        return result
    finally:
        _lock.release()