FROM ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.21

RUN apk add --no-cache py3-requests

COPY run.sh /
COPY evo_bridge.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
