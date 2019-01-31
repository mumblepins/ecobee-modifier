FROM python:3.6-alpine



COPY *.py requirements.txt /ecobee/

RUN set -ex; \
    apk add --no-cache --virtual .build-deps git ;\
    cd /ecobee/ ;\
    pip install -r requirements.txt ;\
    mkdir -p /ecobee/config ;\
    apk del --no-cache .build-deps

VOLUME /ecobee/config

WORKDIR /ecobee/config
CMD ["python", "/ecobee/ecobee.py"]