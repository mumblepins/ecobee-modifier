FROM python:3.6-alpine



COPY *.py requirements.txt /ecobee/

RUN set -ex; \
    cd /ecobee/ ;\
    pip install -r requirements.txt ;\
    mkdir -p /ecobee/config

VOLUME /ecobee/config

WORKDIR /ecobee/config
CMD ["python", "/ecobee/ecobee.py"]