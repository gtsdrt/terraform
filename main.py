import os
import subprocess
import threading

from fastapi import FastAPI, Header, HTTPException

app = FastAPI(title="Terraform Executor")

TF_DIR = os.getenv("TF_DIR", "/app/tf")
API_KEY = os.getenv("EXECUTOR_API_KEY", "")
ALLOWED = {"init", "plan", "apply", "destroy", "output", "validate"}

# 防止并发执行同一 state
_lock = threading.Lock()

def terraform_env() -> dict:
    env = os.environ.copy()
    # 桥接 Container Apps 的 identity 端点给只认 MSI_ENDPOINT 的客户端
    if env.get("IDENTITY_ENDPOINT") and not env.get("MSI_ENDPOINT"):
        env["MSI_ENDPOINT"] = env["IDENTITY_ENDPOINT"]
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