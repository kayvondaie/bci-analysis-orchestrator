# hash:sha256:4e8edf05f6e3a489fa6ace320e8b7f5111cb87d020ed136696c48754a60336df
ARG REGISTRY_HOST
FROM $REGISTRY_HOST/codeocean/jupyterlab:3.4.4-miniconda4.12.0-python3.9-ubuntu20.04

ARG DEBIAN_FRONTEND=noninteractive
ARG GIT_ASKPASS
COPY git-ask-pass /

ENV CODEOCEAN_TOKEN=cop_ZGE5YjljNTc5YTZhNDVhMzg4NjdiNDE2ZjYwYjZjYzVsYUtodXFPYnZFWmdvTzhrdTZVZjhTbmFlOXF0QUtzTTNjODlmMzhj

RUN pip install -U --no-cache-dir \
    codeocean==0.15.0
