ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-requests

COPY run.sh /
COPY evo_bridge.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
