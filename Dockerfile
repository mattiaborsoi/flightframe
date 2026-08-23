FROM python:3.12-slim

# cairosvg needs the cairo runtime. Inter is the one deliberate poster face:
# cairo resolves font families against whatever the host happens to have, and
# a silently substituted face shifts every baseline and width estimate in the
# layout (the deployed board once drew its route arrow through "London–LHR"
# because the container fell back to the wider DejaVu). DejaVu stays as the
# in-container fallback only.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        fonts-inter \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY flightframe ./flightframe

ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    OUT_DIR=/out \
    CACHE_DIR=/cache

VOLUME ["/data", "/out", "/cache"]

ENTRYPOINT ["python", "-m", "flightframe.cli"]
CMD ["where"]
