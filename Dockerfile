FROM ubuntu:24.04

ARG TERRAFORM_VERSION=1.12.2


RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 安装 Terraform
RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin \
    && rm /tmp/tf.zip \
    && terraform version

WORKDIR /app

COPY requirements.txt .
# Ubuntu 24.04 的 pip 默认禁止装到系统环境，需加 --break-system-packages
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .
COPY tf/ /app/tf
COPY tf-test/ /app/tf-test

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]