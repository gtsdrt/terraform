# ========== 阶段 1：构建机有外网，预下载 Terraform + Provider ==========
FROM ubuntu:24.04 AS builder

ARG TERRAFORM_VERSION=1.12.2

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 下载 Terraform 二进制（构建阶段执行，GitHub Actions 有外网）
RUN curl -fsSL "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" -o /tmp/tf.zip \
    && unzip /tmp/tf.zip -d /usr/local/bin \
    && rm /tmp/tf.zip

WORKDIR /tmp

# 把两个环境的代码都复制进来，分别 init 并收集所需 provider
COPY tf/ ./tf/
RUN cd tf && terraform init -input=false -backend=false

COPY tf-test/ ./tf-test/
RUN cd tf-test && terraform init -input=false -backend=false

# 统一镜像到本地目录（两个项目的 provider 都会合并进来）
RUN cd tf && terraform providers mirror /terraform-providers

# ========== 阶段 2：最终运行镜像（无需 curl/unzip，无需外网） ==========
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 阶段复制 terraform 二进制
COPY --from=builder /usr/local/bin/terraform /usr/local/bin/terraform

# 从 builder 阶段复制预下载的 provider
COPY --from=builder /terraform-providers /usr/share/terraform/providers

# 配置 Terraform 强制使用本地镜像，禁止回退联网
RUN mkdir -p /root/.terraform.d && \
    cat > /root/.terraform.d/terraform.rc <<'EOF'
provider_installation {
  filesystem_mirror {
    path    = "/usr/share/terraform/providers"
    include = ["registry.terraform.io/*/*"]
  }
  direct {
    exclude = ["registry.terraform.io/*/*"]
  }
}
EOF

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY main.py .
COPY tf/ /app/tf
COPY tf-test/ /app/tf-test

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]