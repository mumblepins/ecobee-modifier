FROM python:3.6-alpine

RUN set -ex; \
    pip install \
        pyowm \
        pyecobee ;\
    mkdir -p /ecobee/config

COPY ecobee*.py utils.py /ecobee/
VOLUME /ecobee/config

WORKDIR /ecobee/config
CMD ["python", "/ecobee/ecobee.py"]