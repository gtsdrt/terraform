FROM python:3.12-slim

WORKDIR /app

# 先复制依赖文件，利用 Docker 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY main.py .

# 创建非 root 用户
RUN useradd --create-home appuser
USER appuser

# Container App 默认注入 PORT 环境变量，默认 80
EXPOSE 80

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-80}"]