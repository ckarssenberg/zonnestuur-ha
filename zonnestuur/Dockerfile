# Zonnestuur als Home Assistant-add-on. Alleen Python-standaardbibliotheek, dus geen pip nodig.
FROM python:3.12-alpine
RUN apk add --no-cache tzdata
ENV PYTHONUNBUFFERED=1 ZONNESTUUR_INGRESS_ONLY=1
WORKDIR /app
COPY app/ /app/
CMD ["python3", "/app/run.py"]
LABEL io.hass.type="addon" io.hass.name="Zonnestuur"
