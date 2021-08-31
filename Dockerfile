FROM python:alpine AS base

RUN set -ex \
    && apk update \
    && apk add --no-cache \
        bind-tools

FROM base as packages

COPY requirements.txt /

RUN set -ex \
    && pip3 install -r requirements.txt

FROM packages

COPY generate-dns-records.py /
COPY entrypoint.sh /

USER nobody
ENTRYPOINT ["/entrypoint.sh"]
CMD ["update-records"]