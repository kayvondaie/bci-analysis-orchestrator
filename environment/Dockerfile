# hash:sha256:b475000773406743e640186b07238bfda6e331c2cc724130e1abfd40770e4fe6
ARG REGISTRY_HOST
FROM $REGISTRY_HOST/codeocean/jupyterlab:3.4.4-miniconda4.12.0-python3.9-ubuntu20.04

ARG DEBIAN_FRONTEND=noninteractive
ARG GIT_ASKPASS
COPY git-ask-pass /

ENV CODEOCEAN_TOKEN=cop_ZGE5YjljNTc5YTZhNDVhMzg4NjdiNDE2ZjYwYjZjYzVsYUtodXFPYnZFWmdvTzhrdTZVZjhTbmFlOXF0QUtzTTNjODlmMzhj
ENV SESSION=850378:2026-05-27

RUN pip install -U --no-cache-dir \
    codeocean==0.15.0

ADD "https://github.com/coder/code-server/releases/download/v4.100.3/code-server-4.100.3-linux-amd64.tar.gz" /.code-server/code-server.tar.gz

RUN cd /.code-server \
    && tar -xvf code-server.tar.gz \
    && rm code-server.tar.gz \
    && ln -s /.code-server/code-server-4.100.3-linux-amd64/bin/code-server /usr/bin/code-server \
    && mkdir -p /.vscode/extensions

RUN code-server --extensions-dir="/.vscode/extensions" --install-extension ms-python.python@2025.14.0 \
    && code-server --extensions-dir="/.vscode/extensions" --install-extension ms-toolsai.jupyter@2025.4.1 \
    && code-server --extensions-dir="/.vscode/extensions" --install-extension reditorsupport.r@2.8.6 \
    && code-server --extensions-dir="/.vscode/extensions" --install-extension saoudrizwan.claude-dev@3.35.1
